// Package main: Skeen (Algoritmo 3 do artigo) entre blockchains CometBFT — aplicação ABCI.
//
// O consenso de cada blockchain faz o papel do A-broadcast do artigo: o START de uma tx (Alg. 3, l. 7) e o
// FINAL_LOCAL (l. 18) são transações ordenadas pelo CometBFT, e FinalizeBlock as processa de forma determinística em
// todos os validadores. Votos (TS_LOCAL, ACK_LOCAL), certificados f+1 e DELIVER_LOCAL trafegam fora do consenso
// (skeen.go). Toda tx recebe timestamp (C10); a entrega segue o tryDeliver do Alg. 1 (l. 20–25).
//
// Formato das transações:
//
//	L|<id>                   tx local (um destino: a própria blockchain)
//	S|<id>|<chain1,chain2>   START de tx global com os destinos (nomes das blockchains)
//	F|<hex de JSON>          FINAL_LOCAL: {id, certs TS de todos os destinos}
package main

import (
	"container/heap"
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"sort"
	"strings"
	"sync"

	abci "github.com/cometbft/cometbft/abci/types"
	cmtlog "github.com/cometbft/cometbft/libs/log"
)

// TS é um timestamp do Skeen: ⟨clock, grupo⟩ (Alg. 3, l. 10), ordenado por clock e, no empate, pelo grupo.
type TS struct {
	Clock uint64 `json:"c"`
	Chain string `json:"g"`
}

func (a TS) Less(b TS) bool {
	if a.Clock != b.Clock {
		return a.Clock < b.Clock
	}
	return a.Chain < b.Chain
}

// gtx: estado de uma tx nesta réplica.
type gtx struct {
	id        string
	dest      []string
	local     TS
	final     TS
	finalized bool // final[m] conhecido (FINAL_LOCAL aplicado, ou tx local)
	acked     bool // ACK de todos os destinos (DELIVER_LOCAL), ou tx local
	delivered bool
	global    bool
	env       string // envelope do cliente para o Fabric (base64), quando veio no START: relay CometBFT → Fabric
}

// heap de (timestamp, id) — usado para os pendentes sem final (pelo local) e para os finalizados (pelo final)
type tsItem struct {
	ts TS
	id string
}
type tsHeap []tsItem

func (h tsHeap) Len() int           { return len(h) }
func (h tsHeap) Less(i, j int) bool { return h[i].ts.Less(h[j].ts) }
func (h tsHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *tsHeap) Push(x any)        { *h = append(*h, x.(tsItem)) }
func (h *tsHeap) Pop() any          { o := *h; n := len(o); x := o[n-1]; *h = o[:n-1]; return x }

// SkeenApp é o estado da aplicação Skeen em um validador de uma blockchain.
type SkeenApp struct {
	abci.BaseApplication
	me     string // nome desta blockchain (grupo Gi)
	logger cmtlog.Logger
	net    *Net // votos, certificados e DELIVER_LOCAL (skeen.go)

	mu       sync.Mutex
	clock    uint64          // relógio lógico (Alg. 1, l. 1)
	txs      map[string]*gtx // local[], final[], acked, delivered (Alg. 1, l. 2–5)
	notFinal tsHeap          // tx sem final, pelo timestamp local
	finals   tsHeap          // tx com final e não entregues, pelo final
	seq      uint64          // nº de entregas
	// certificados de ACK que chegaram (DELIVER_LOCAL) antes do FINAL: verificados na chegada e acumulados por grupo,
	// para que uma mensagem inválida não apague uma válida (achado B3 do revisor)
	pendDLV map[string]map[string]Cert
	pend    map[string]*gtx // globais ainda não entregues (a retransmissão percorre só estas, achado 9)
}

func NewSkeenApp(me string, logger cmtlog.Logger) *SkeenApp {
	return &SkeenApp{me: me, logger: logger, txs: map[string]*gtx{}, pendDLV: map[string]map[string]Cert{}, pend: map[string]*gtx{}}
}

// gid: identidade de uma tx global, "<id do cliente>@<destinos em ordem>". Um id no formato do Fabric
// ("CROSS_canal1-canal2_<sufixo>", destinos dentro do id) é a própria identidade, a mesma nas duas plataformas.
func gid(id string, dest []string) string {
	if strings.HasPrefix(id, "CROSS_") {
		return id
	}
	d := append([]string(nil), dest...)
	sort.Strings(d)
	return id + "@" + strings.Join(d, ",")
}

// clientID e destOf desfazem o gid (o id que o cliente escolheu e os destinos que ele declarou).
// crossDestOf: destinos (ordenados) de um id do Fabric "CROSS_<d1>-<d2>_<sufixo>", ou nil.
func crossDestOf(id string) []string {
	if !strings.HasPrefix(id, "CROSS_") {
		return nil
	}
	p := strings.Split(id, "_")
	if len(p) < 2 || p[1] == "" { // paridade com parseDest do Fabric: "CROSS_canal1-canal2" sem sufixo também vale
		return nil
	}
	d := strings.Split(p[1], "-")
	sort.Strings(d)
	return d
}

func clientID(g string) string {
	if i := strings.Index(g, "@"); i >= 0 {
		return g[:i]
	}
	return g
}

func destOf(g string) []string {
	if d := crossDestOf(g); d != nil {
		return d
	}
	if i := strings.Index(g, "@"); i >= 0 {
		return strings.Split(g[i+1:], ",")
	}
	return nil
}

func contains(xs []string, x string) bool {
	for _, y := range xs {
		if y == x {
			return true
		}
	}
	return false
}

type parsed struct {
	kind string // L, S, F
	id   string
	dest []string
	fin  *finalMsg
	env  string // 4º campo opcional do S: o envelope assinado pelo cliente para o Fabric, em base64
}

type finalMsg struct {
	ID    string     `json:"id,omitempty"`
	IDs   []string   `json:"ids,omitempty"` // FINAL_LOCAL em lote: várias tx com os certificados partilhados
	Certs []Cert     `json:"certs,omitempty"`
	Fab   []alg3Cert `json:"fab,omitempty"` // certificados do Fabric, cada um uma vez só
}

func parseTx(b []byte) (parsed, bool) {
	s := string(b)
	switch {
	case strings.HasPrefix(s, "L|"):
		if id := s[2:]; id != "" && !strings.Contains(id, "|") {
			return parsed{kind: "L", id: id}, true
		}
	case strings.HasPrefix(s, "S|"):
		p := strings.Split(s, "|")
		env := ""
		if len(p) == 4 && strings.HasPrefix(p[1], "CROSS_") {
			// "S|<id>|<destinos>|<envelope>": o envelope tem de ser o da própria tx (mesmo TxID), senão o relay ao
			// Fabric levaria outra tx; a assinatura do cliente quem confere é o Fabric (alg3InnerStart)
			raw, err := base64.StdEncoding.DecodeString(p[3])
			if err != nil || envelopeTxID(raw) != p[1] {
				return parsed{}, false
			}
			env, p = p[3], p[:3]
		}
		if len(p) == 3 && p[1] != "" && p[2] != "" && !strings.ContainsAny(p[1], "@ \t\r\n") {
			d := strings.Split(p[2], ",")
			if cd := crossDestOf(p[1]); strings.HasPrefix(p[1], "CROSS_") {
				// id do Fabric: os destinos declarados têm de ser os do próprio id
				ds := append([]string(nil), d...)
				sort.Strings(ds)
				if strings.Join(ds, ",") != strings.Join(cd, ",") {
					return parsed{}, false
				}
			}
			if len(d) >= 2 {
				// identidade da mensagem = id do cliente + destinos (achado R1 do revisor): o mesmo id com destinos
				// diferentes são mensagens distintas, em vez de duas versões da mesma com finais divergentes
				return parsed{kind: "S", id: gid(p[1], d), dest: d, env: env}, true
			}
		}
	case strings.HasPrefix(s, "F|"):
		raw, err := hex.DecodeString(s[2:])
		if err != nil {
			return parsed{}, false
		}
		var f finalMsg
		if json.Unmarshal(raw, &f) != nil || (f.ID == "" && len(f.IDs) == 0) {
			return parsed{}, false
		}
		if f.ID != "" {
			f.IDs = append([]string{f.ID}, f.IDs...)
		}
		return parsed{kind: "F", id: f.IDs[0], fin: &f}, true
	}
	return parsed{}, false
}

// CheckTx: formato válido e, para S, destinos válidos (C4: todos conhecidos, sem repetição, incluindo esta blockchain).
func (a *SkeenApp) CheckTx(_ context.Context, req *abci.RequestCheckTx) (*abci.ResponseCheckTx, error) {
	p, ok := parseTx(req.Tx)
	if !ok {
		return &abci.ResponseCheckTx{Code: 1, Log: "formato desconhecido"}, nil
	}
	if p.kind == "S" && !a.net.startOK(p) {
		return &abci.ResponseCheckTx{Code: 2, Log: "destinos inválidos"}, nil
	}
	return &abci.ResponseCheckTx{Code: 0}, nil
}

// FinalizeBlock: as tx do bloco na ordem decidida pelo consenso (o A-deliver do artigo).
func (a *SkeenApp) FinalizeBlock(_ context.Context, req *abci.RequestFinalizeBlock) (*abci.ResponseFinalizeBlock, error) {
	a.mu.Lock()
	res := make([]*abci.ExecTxResult, len(req.Txs))
	var votes []Vote
	for i, raw := range req.Txs {
		p, ok := parseTx(raw)
		res[i] = &abci.ExecTxResult{Code: 0}
		if !ok {
			res[i].Code = 1
			continue
		}
		switch p.kind {
		case "L": // tx local (C10): recebe timestamp; final = local; não espera ninguém além das globais anteriores
			// espaço de ids próprio ("L|id"): um id de local não colide com o de uma global (achado B4 do revisor)
			k := "L|" + p.id
			if a.txs[k] != nil {
				continue
			}
			a.clock++
			ts := TS{a.clock, a.me}
			a.txs[k] = &gtx{id: p.id, dest: []string{a.me}, local: ts, final: ts, finalized: true, acked: true}
			heap.Push(&a.finals, tsItem{ts, k})
		case "S": // START_LOCAL A-entregue (Alg. 3, l. 8–12)
			if a.txs[p.id] != nil || !a.net.startOK(p) { // C4, determinístico (mesma config em todas as réplicas)
				res[i].Code = 2
				continue
			}
			a.clock++               // l. 9
			ts := TS{a.clock, a.me} // l. 10: local[m] ← ⟨clock, Gi⟩
			x := &gtx{id: p.id, dest: p.dest, local: ts, global: true, env: p.env}
			a.txs[p.id] = x
			a.pend[p.id] = x
			heap.Push(&a.notFinal, tsItem{ts, p.id})
			a.net.trace("START", p.id, ts)
			votes = append(votes, a.net.signVote("TS", p.id, ts)) // l. 11–12: voto TS_LOCAL ao coordenador
		case "F": // FINAL_LOCAL A-entregue (Alg. 3, l. 19–24), em lote
			for _, id := range p.fin.IDs {
				x := a.txs[id]
				if x == nil || !x.global || x.finalized {
					continue
				}
				fin, ok := a.net.checkTSCerts(x, expandCerts(id, p.fin.Certs, p.fin.Fab)) // provas de todos os destinos (C1)
				if !ok {
					a.net.trace("FINAL_REJ", id, TS{})
					continue
				}
				x.final, x.finalized = fin, true // l. 20: final[m] ← máximo dos ts
				if fin.Clock > a.clock {
					a.clock = fin.Clock // l. 21: clock ← max(clock, final[m])
				}
				heap.Push(&a.finals, tsItem{fin, id})
				a.net.trace("FINAL", id, fin)
				votes = append(votes, a.net.signVote("ACK", id, fin)) // l. 23–24: voto ACK_LOCAL
				a.applyDLVLocked(x)                                   // DELIVER_LOCAL que chegou antes do FINAL
			}
		}
	}
	a.tryDeliverLocked() // l. 22
	a.mu.Unlock()
	for _, v := range votes {
		a.net.sendVote(v)
	}
	// coordenador: o certificado TS (ou ACK) do próprio grupo pode ter se formado com votos de outras réplicas antes de
	// este nó aplicar o bloco; reavalia já, em vez de esperar a retransmissão (achado no rastro de 16/11: até 1 s)
	if a.net.coordinator() {
		for _, v := range votes {
			a.net.progress(v.TxID)
		}
	}
	return &abci.ResponseFinalizeBlock{TxResults: res}, nil
}

// onDLV: DELIVER_LOCAL do coordenador (Alg. 3, l. 31–32): certificados de ACK de todos os destinos → acked. Cada
// certificado é verificado na chegada (f+1 assinaturas) e acumulado por grupo; o valor é conferido contra final[m]
// quando o FINAL estiver aplicado (C3).
func (a *SkeenApp) onDLV(id string, certs []Cert) {
	var ok []Cert
	for _, c := range certs {
		if a.net.ackCertOK(id, c) {
			ok = append(ok, c)
		}
	}
	if len(ok) == 0 {
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	x := a.txs[id]
	if x != nil && (!x.global || x.acked) {
		return
	}
	if a.pendDLV[id] == nil {
		a.pendDLV[id] = map[string]Cert{}
	}
	for _, c := range ok {
		if old, tem := a.pendDLV[id][c.Chain]; !tem || (x != nil && x.finalized && old.Val != x.final && c.Val == x.final) {
			a.pendDLV[id][c.Chain] = c
		}
	}
	if x != nil && x.finalized {
		a.applyDLVLocked(x)
		a.tryDeliverLocked()
	}
}

// applyDLVLocked: acked se há ACK de todos os destinos, todos sobre o final[m] desta réplica (C3).
func (a *SkeenApp) applyDLVLocked(x *gtx) {
	if x.acked {
		return
	}
	for _, g := range x.dest {
		if c, ok := a.pendDLV[x.id][g]; !ok || c.Val != x.final {
			return
		}
	}
	x.acked = true
	delete(a.pendDLV, x.id)
	a.net.trace("ACKED", x.id, x.final)
}

// tryDeliverLocked: Alg. 1, l. 20–25, em O(log N) por entrega. Entrega o finalizado de menor final[m] se ele já tem
// ACK de todos os destinos e nenhum pendente sem final tem timestamp local menor que final[m] (l. 22–23).
func (a *SkeenApp) tryDeliverLocked() {
	for {
		for a.notFinal.Len() > 0 {
			if x := a.txs[a.notFinal[0].id]; x.finalized || x.delivered {
				heap.Pop(&a.notFinal)
				continue
			}
			break
		}
		if a.finals.Len() == 0 {
			return
		}
		top := a.finals[0]
		x := a.txs[top.id]
		if x.delivered {
			heap.Pop(&a.finals)
			continue
		}
		if !x.acked {
			return
		}
		if a.notFinal.Len() > 0 && !top.ts.Less(a.notFinal[0].ts) {
			return
		}
		heap.Pop(&a.finals)
		x.delivered = true
		delete(a.pend, top.id)
		x.env = ""
		a.seq++
		a.net.deliver(a.seq, x) // deliver(m) (l. 25)
	}
}

// pendentes para retransmissão (skeen.go)
func (a *SkeenApp) pendingSnapshot() []gtx {
	a.mu.Lock()
	defer a.mu.Unlock()
	out := make([]gtx, 0, len(a.pend))
	for _, x := range a.pend {
		out = append(out, *x)
	}
	return out
}

func (a *SkeenApp) lookup(id string) (gtx, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	x := a.txs[id]
	if x == nil {
		return gtx{}, false
	}
	return *x, true
}

func (a *SkeenApp) Query(_ context.Context, req *abci.RequestQuery) (*abci.ResponseQuery, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	switch string(req.Data) {
	case "clock":
		return &abci.ResponseQuery{Value: []byte(utoa(a.clock))}, nil
	case "entregues":
		return &abci.ResponseQuery{Value: []byte(utoa(a.seq))}, nil
	}
	if x := a.txs[string(req.Data)]; x != nil {
		b, _ := json.Marshal(map[string]any{"local": x.local, "final": x.final, "finalized": x.finalized,
			"acked": x.acked, "delivered": x.delivered})
		return &abci.ResponseQuery{Value: b}, nil
	}
	return &abci.ResponseQuery{Code: 1}, nil
}

func utoa(v uint64) string {
	b, _ := json.Marshal(v)
	return string(b)
}
