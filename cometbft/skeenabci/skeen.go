package main

// Camada fora do consenso do Algoritmo 3: votos TS_LOCAL/ACK_LOCAL (l. 12, 24) ao coordenador do grupo; certificados
// f+1 (l. 13–15, 25–27) aos coordenadores dos destinos; FINAL_LOCAL submetido ao consenso do próprio grupo (l. 18);
// DELIVER_LOCAL (l. 30) do coordenador às réplicas do grupo. O coordenador é a réplica 0 de cada blockchain (fixo
// nesta versão; ver PLANO.md). Assinaturas ed25519 com a chave de validador do próprio CometBFT.
//
// Recuperação por pedido (NEED, 16/11/2026, achado B1 do revisor): quem espera pede o que falta, e quem tem responde
// com o que guardou, qualquer que seja o seu próprio estado (certificados e votos ficam guardados). Assim uma
// mensagem perdida (fila cheia, conexão caída) não trava ninguém:
//   - coordenador sem o TS (ou ACK) de outro destino → NEED TS/ACK ao coordenador dele; o NEED TS leva o START, e um
//     coordenador que não conhece a tx o reenvia ao próprio consenso (relay do R-multicast, l. 5);
//   - coordenador sem o próprio certificado → NEED VOTE_TS/VOTE_ACK às réplicas do grupo, que reenviam o voto;
//   - réplica com FINAL aplicado e sem DELIVER_LOCAL → NEED DLV ao coordenador.
import (
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/rand"
	"net"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/cometbft/cometbft/crypto"
	"github.com/cometbft/cometbft/crypto/ed25519"
)

// Config: uma por réplica (gerada pelo rodar_local.sh).
type Config struct {
	Chain  string                 `json:"chain"`
	Listen string                 `json:"listen"`
	F      int                    `json:"f"` // obsoleto: f de cada grupo = (n-1)/3 (ver fOf)
	Chains map[string]ChainConfig `json:"chains"`
	// Skeen entre plataformas: grupos Fabric que podem ser destino junto com esta blockchain, e os endereços em que o
	// coordenador desta blockchain escuta o fio do Fabric (os alg3Addr deste grupo vistos pelo Fabric)
	Fabric     map[string]FabricGroup `json:"fabric,omitempty"`
	WireListen []string               `json:"wire_listen,omitempty"`
}

type ChainConfig struct {
	Replicas []Replica `json:"replicas"` // replicas[0] = coordenador
	RPC      string    `json:"rpc"`      // RPC do nó do coordenador (para submeter FINAL_LOCAL e relay de START)
}

type Replica struct {
	Addr   string `json:"addr"`
	PubKey string `json:"pubkey"` // ed25519, base64 (priv_validator_key.json)
}

// Vote: voto TS_LOCAL/ACK_LOCAL de uma réplica sobre (tx, valor). Os destinos NÃO vão no voto (não seriam assinados):
// o coordenador usa os do START que ele mesmo aplicou (achado do revisor, genuinidade).
type Vote struct {
	Kind    string `json:"k"`
	Chain   string `json:"g"`
	TxID    string `json:"x"`
	Val     TS     `json:"v"`
	Replica int    `json:"r"`
	Sig     []byte `json:"s"`
}

// Cert: f+1 votos de réplicas distintas do mesmo grupo sobre o mesmo (tipo, tx, valor).
type Cert struct {
	Kind  string         `json:"k"`
	Chain string         `json:"g"`
	TxID  string         `json:"x"`
	Val   TS             `json:"v"`
	Sigs  map[int][]byte `json:"s,omitempty"`
	Fab   *alg3Cert      `json:"fab,omitempty"` // certificado de um grupo Fabric, inteiro (pode cobrir várias tx)
}

type Msg struct {
	Type  string `json:"t"` // VOTE, CERT, DLV, NEED
	Vote  *Vote  `json:"vote,omitempty"`
	Cert  *Cert  `json:"cert,omitempty"`
	TxID  string `json:"x,omitempty"`
	Certs []Cert `json:"certs,omitempty"`
	Kind  string `json:"k,omitempty"`  // NEED: TS, ACK, VOTE_TS, VOTE_ACK, DLV
	From  string `json:"f,omitempty"`  // NEED: grupo de quem pede
	Rep   int    `json:"r,omitempty"`  // NEED: réplica de quem pede (a resposta vai a ela)
	Start string `json:"st,omitempty"` // NEED TS: o START (para o relay)
	// DLVB (lote de DELIVER_LOCAL): várias tx com os certificados partilhados (os do Fabric aparecem uma vez só)
	TxIDs []string   `json:"xs,omitempty"`
	Fab   []alg3Cert `json:"fab,omitempty"`
}

// signBytes: o que uma réplica assina num voto = o resumo do certificado de 1 item NO FORMATO DO FABRIC
// (fabricwire.go), para que o mesmo certificado valha no Fabric (Skeen entre plataformas, 16/11/2026). Item TS:
// {tx, clock local, destinos}; item ACK: {tx, clock e grupo do final, destinos}; Seq = 0.
func signBytes(kind, chain, tx string, v TS) []byte {
	d := wireCert(kind, chain, tx, v).digest()
	return d[:]
}

func wireCert(kind, chain, tx string, v TS) *alg3Cert {
	it := alg3Item{TxID: tx, Clock: v.Clock, Dest: destOf(tx)}
	if kind == "ACK" {
		it.Node = v.Chain
	}
	return &alg3Cert{Kind: kind, Group: chain, Items: []alg3Item{it}}
}

// toWire: certificado CometBFT no formato do Fabric (Sig.ID = "ed25519:<i>", verificado com SKEEN_FOREIGN no Fabric).
func toWire(c Cert) alg3Cert {
	w := *wireCert(c.Kind, c.Chain, c.TxID, c.Val)
	idx := make([]int, 0, len(c.Sigs))
	for i := range c.Sigs {
		idx = append(idx, i)
	}
	sort.Ints(idx)
	for _, i := range idx {
		w.Sigs = append(w.Sigs, alg3Sig{ID: []byte("ed25519:" + strconv.Itoa(i)), Sig: c.Sigs[i]})
	}
	return w
}

type Net struct {
	cfg  Config
	me   int // índice desta réplica no grupo
	priv crypto.PrivKey
	pubs map[string][]crypto.PubKey
	app  *SkeenApp
	drop float64 // SKEEN_DROP: fração de mensagens descartadas de propósito (teste de recuperação)

	mu       sync.Mutex
	votes    map[string]map[int][]byte   // tipo|tx|valor → réplica → assinatura (só no coordenador)
	myCerts  map[string]*Cert            // tipo|tx → certificado do próprio grupo (coordenador; guardado)
	certSent map[string]bool             // tipo|tx → certificado próprio já enviado aos destinos
	certs    map[string]map[string]*Cert // tipo|tx → grupo → certificado (coordenador; guardado)
	finalAt  map[string]time.Time        // FINAL_LOCAL submetido
	dlvAt    map[string]time.Time        // DELIVER_LOCAL enviado ao grupo
	relayAt  map[string]time.Time        // START reenviado ao próprio consenso (relay)
	needAt   map[string]time.Time        // último NEED por tx
	startAt  map[string]time.Time        // START aplicado aqui
	myVotes  map[string]Vote             // tipo|tx → meu voto (guardado, para responder NEED)
	senders  map[string]chan Msg
	fsigners map[string][]fabricSigner // orderers fixados de cada grupo Fabric
	fabCoord map[string]int            // pista do coordenador de cada grupo Fabric (réplica 1..n, do Src do CERT)
	wireQ    map[string]chan alg3Msg
	fabOK    sync.Map // certificados do Fabric já verificados
	// prontas para o FINAL_LOCAL / DELIVER_LOCAL em lote (batchLoop, 16/11/2026: um FINAL por tx carregava o
	// certificado inteiro do lote do Fabric, ~96 KB cada, e lotava o mempool do CometBFT)
	wireIn     chan alg3Msg // mensagens do fio do Fabric à espera dos trabalhadores (listenWire)
	finalReady map[string]bool
	dlvReady   map[string]bool
	out        *bufio.Writer
	tr         *bufio.Writer
	outMu      sync.Mutex
}

func NewNet(cfg Config, priv crypto.PrivKey, outDir string) (*Net, error) {
	n := &Net{cfg: cfg, priv: priv, pubs: map[string][]crypto.PubKey{}, votes: map[string]map[int][]byte{},
		myCerts: map[string]*Cert{}, certSent: map[string]bool{}, certs: map[string]map[string]*Cert{},
		finalAt: map[string]time.Time{}, dlvAt: map[string]time.Time{}, relayAt: map[string]time.Time{},
		needAt: map[string]time.Time{}, startAt: map[string]time.Time{}, myVotes: map[string]Vote{},
		senders: map[string]chan Msg{}, me: -1, fsigners: map[string][]fabricSigner{}, fabCoord: map[string]int{},
		wireQ: map[string]chan alg3Msg{}, finalReady: map[string]bool{}, dlvReady: map[string]bool{}}
	for g, fg := range cfg.Fabric {
		sg, err := parseFabricSigners(fg.PEMs)
		if err != nil {
			return nil, fmt.Errorf("grupo Fabric %s: %v", g, err)
		}
		n.fsigners[g] = sg
	}
	if d, err := strconv.ParseFloat(os.Getenv("SKEEN_DROP"), 64); err == nil {
		n.drop = d
	}
	mine := base64.StdEncoding.EncodeToString(priv.PubKey().Bytes())
	for g, cc := range cfg.Chains {
		for i, r := range cc.Replicas {
			b, err := base64.StdEncoding.DecodeString(r.PubKey)
			if err != nil || len(b) != ed25519.PubKeySize {
				return nil, fmt.Errorf("chave pública inválida de %s[%d]", g, i)
			}
			n.pubs[g] = append(n.pubs[g], ed25519.PubKey(b))
			if g == cfg.Chain && r.PubKey == mine {
				n.me = i
			}
		}
	}
	if n.me < 0 {
		return nil, fmt.Errorf("minha chave não está na lista de réplicas de %s", cfg.Chain)
	}
	if outDir != "" {
		f, err := os.Create(fmt.Sprintf("%s/%s_%d.txt", outDir, cfg.Chain, n.me))
		if err != nil {
			return nil, err
		}
		n.out = bufio.NewWriter(f)
		t, err := os.Create(fmt.Sprintf("%s/%s_%d_trace.jsonl", outDir, cfg.Chain, n.me))
		if err != nil {
			return nil, err
		}
		n.tr = bufio.NewWriter(t)
		go func() {
			for range time.Tick(200 * time.Millisecond) {
				n.outMu.Lock()
				n.out.Flush()
				n.tr.Flush()
				n.outMu.Unlock()
			}
		}()
	}
	return n, nil
}

// fOf: f do grupo g, n = 3f+1 réplicas (o fi do artigo, por grupo).
func (n *Net) fOf(g string) int {
	if fg, ok := n.cfg.Fabric[g]; ok {
		return (len(fg.PEMs) - 1) / 3
	}
	return (len(n.cfg.Chains[g].Replicas) - 1) / 3
}
func (n *Net) coordinator() bool { return n.me == 0 }
func (n *Net) coordAddr(g string) string {
	return n.cfg.Chains[g].Replicas[0].Addr
}

// validDest (determinístico, mesma config em todas as réplicas): ≥ 2 destinos, todos blockchains conhecidas, sem
// repetição, incluindo a própria (C4; achado B2 do revisor).
func (n *Net) validDest(dest []string) bool {
	if len(dest) < 2 {
		return false
	}
	vis := map[string]bool{}
	for _, g := range dest {
		if _, ok := n.cfg.Chains[g]; (!ok && !n.isFabric(g)) || vis[g] {
			return false
		}
		vis[g] = true
	}
	return vis[n.cfg.Chain]
}

// startOK: START aceitável (determinístico, só depende da tx e da configuração): destinos válidos (C4) e, se algum
// destino é do Fabric, o envelope do cliente junto (reconferência do revisor, 17/11/2026: sem ele o coordenador não
// consegue levar o START ao Fabric, e um cliente que mandasse a global só ao CometBFT travaria esta blockchain).
func (n *Net) startOK(p parsed) bool {
	if !n.validDest(p.dest) {
		return false
	}
	if p.env == "" {
		for _, g := range p.dest {
			if n.isFabric(g) {
				return false
			}
		}
	}
	return true
}

// trace: evento com o relógio de parede em µs (para a linha do tempo da página de validação).
func (n *Net) trace(ev, id string, v TS) {
	if ev == "START" {
		n.mu.Lock()
		n.startAt[id] = time.Now()
		n.mu.Unlock()
	}
	if n.tr == nil {
		return
	}
	b, _ := json.Marshal(map[string]any{"t": time.Now().UnixMicro(), "ev": ev, "x": clientID(id), "c": n.cfg.Chain,
		"r": n.me, "v": v})
	n.outMu.Lock()
	n.tr.Write(b)
	n.tr.WriteByte('\n')
	n.outMu.Unlock()
}

// deliver: deliver(m) do Alg. 1, l. 25 — a sequência de entregas desta réplica (verificada pelo verifica.py).
func (n *Net) deliver(seq uint64, x *gtx) {
	n.trace("DELIVER", x.id, x.final)
	if n.out == nil {
		return
	}
	n.outMu.Lock()
	fmt.Fprintf(n.out, "%d %s %d %s %d\n", seq, clientID(x.id), x.final.Clock, x.final.Chain, time.Now().UnixMicro())
	n.outMu.Unlock()
}

// ---------- votos e certificados ----------

func (n *Net) signVote(kind, tx string, v TS) Vote {
	sig, err := n.priv.Sign(signBytes(kind, n.cfg.Chain, tx, v))
	if err != nil {
		panic(err)
	}
	vt := Vote{Kind: kind, Chain: n.cfg.Chain, TxID: tx, Val: v, Replica: n.me, Sig: sig}
	n.mu.Lock()
	n.myVotes[kind+"|"+tx] = vt
	n.mu.Unlock()
	return vt
}

// sendVote: o voto vai só ao coordenador do próprio grupo (l. 12, 24).
func (n *Net) sendVote(v Vote) {
	if n.coordinator() {
		n.onVote(v)
		return
	}
	n.send(n.coordAddr(n.cfg.Chain), Msg{Type: "VOTE", Vote: &v})
}

// onVote (coordenador): junta f+1 votos válidos e distintos sobre o mesmo valor → certificado (l. 13–15, 25–27).
func (n *Net) onVote(v Vote) {
	if !n.coordinator() || v.Chain != n.cfg.Chain || v.Replica < 0 || v.Replica >= len(n.pubs[v.Chain]) ||
		(v.Kind != "TS" && v.Kind != "ACK") {
		return
	}
	if !n.pubs[v.Chain][v.Replica].VerifySignature(signBytes(v.Kind, v.Chain, v.TxID, v.Val), v.Sig) {
		return
	}
	key := fmt.Sprintf("%s|%s|%d|%s", v.Kind, v.TxID, v.Val.Clock, v.Val.Chain)
	ck := v.Kind + "|" + v.TxID
	n.mu.Lock()
	if n.votes[key] == nil {
		n.votes[key] = map[int][]byte{}
	}
	n.votes[key][v.Replica] = v.Sig
	novo := false
	if n.myCerts[ck] == nil && len(n.votes[key]) >= n.fOf(v.Chain)+1 {
		sigs := map[int][]byte{}
		for r, s := range n.votes[key] {
			sigs[r] = s
			if len(sigs) == n.fOf(v.Chain)+1 {
				break
			}
		}
		n.myCerts[ck] = &Cert{Kind: v.Kind, Chain: v.Chain, TxID: v.TxID, Val: v.Val, Sigs: sigs}
		novo = true
	}
	n.mu.Unlock()
	if novo {
		n.trace(v.Kind+"CERT", v.TxID, v.Val)
		n.progress(v.TxID)
	}
}

// verifyCert: f+1 assinaturas válidas de réplicas distintas do grupo do certificado.
func (n *Net) verifyCert(c Cert) bool {
	if n.isFabric(c.Chain) {
		return n.verifyFabricView(c)
	}
	ps := n.pubs[c.Chain]
	if c.Kind == "TS" && c.Val.Chain != c.Chain { // o resumo do TS não leva o grupo do valor: conferir aqui
		return false
	}
	if len(ps) == 0 || (c.Kind != "TS" && c.Kind != "ACK") {
		return false
	}
	ok := 0
	for r, s := range c.Sigs {
		if r >= 0 && r < len(ps) && ps[r].VerifySignature(signBytes(c.Kind, c.Chain, c.TxID, c.Val), s) {
			ok++
		}
	}
	return ok >= n.fOf(c.Chain)+1
}

// onCert (coordenador): guarda o certificado (de qualquer destino, inclusive o próprio) e avança a tx.
func (n *Net) onCert(c Cert) {
	if !n.coordinator() || !n.verifyCert(c) {
		return
	}
	ck := c.Kind + "|" + c.TxID
	n.mu.Lock()
	if n.certs[ck] == nil {
		n.certs[ck] = map[string]*Cert{}
	}
	novo := n.certs[ck][c.Chain] == nil
	if novo {
		n.certs[ck][c.Chain] = &c
	}
	n.mu.Unlock()
	if novo && c.Chain != n.cfg.Chain {
		n.trace(c.Kind+"CERT_RX", c.TxID, c.Val)
	}
	n.progress(c.TxID)
}

// progress (coordenador): com a tx conhecida (START aplicado aqui), envia os certificados próprios aos destinos
// (R-multicast, l. 15 e 27, com os destinos do START), submete o FINAL_LOCAL com TS de todos os destinos (l. 16–18) e
// envia o DELIVER_LOCAL com ACK de todos os destinos (l. 28–30).
func (n *Net) progress(id string) {
	if !n.coordinator() {
		return
	}
	x, ok := n.app.lookup(id)
	if !ok {
		return
	}
	var envia []*Cert
	n.mu.Lock()
	for _, k := range []string{"TS", "ACK"} {
		if c := n.myCerts[k+"|"+id]; c != nil && !n.certSent[k+"|"+id] {
			n.certSent[k+"|"+id] = true
			envia = append(envia, c)
		}
	}
	ts, acks := n.certs["TS|"+id], n.certs["ACK|"+id]
	if !x.finalized && haveAll(ts, x.dest) && time.Since(n.finalAt[id]) > 2*time.Second {
		n.finalAt[id] = time.Now()
		n.finalReady[id] = true // o batchLoop junta num FINAL_LOCAL em lote
	}
	if x.finalized && !x.delivered && haveAll(acks, x.dest) && time.Since(n.dlvAt[id]) > 2*time.Second {
		n.dlvAt[id] = time.Now()
		n.dlvReady[id] = true // idem, DELIVER_LOCAL em lote
	}
	n.mu.Unlock()
	for _, c := range envia {
		for _, g := range x.dest {
			switch {
			case g == n.cfg.Chain:
				n.onCert(*c)
			case n.isFabric(g): // ao coordenador do Fabric, no formato dele
				n.sendFab(g, alg3Msg{Type: "CERT", From: n.cfg.Chain, Certs: []alg3Cert{toWire(*c)}, Src: n.me + 1})
			default:
				n.send(n.coordAddr(g), Msg{Type: "CERT", Cert: c})
			}
		}
	}
}

// batchLoop (coordenador): a cada 10 ms junta as tx prontas num FINAL_LOCAL em lote (l. 18, como o alg3Final do Fabric)
// e num DELIVER_LOCAL em lote (l. 30), até 500 tx cada, com os certificados do Fabric sem repetição.
func (n *Net) batchLoop() {
	for range time.Tick(10 * time.Millisecond) {
		for _, kind := range []string{"TS", "ACK"} {
			n.mu.Lock()
			pronto := n.finalReady
			if kind == "ACK" {
				pronto = n.dlvReady
			}
			var ids []string
			for id := range pronto {
				ids = append(ids, id)
				delete(pronto, id)
				if len(ids) == 500 {
					break
				}
			}
			porID := make(map[string][]Cert, len(ids))
			for _, id := range ids {
				for _, c := range n.certs[kind+"|"+id] {
					porID[id] = append(porID[id], *c)
				}
			}
			n.mu.Unlock()
			if len(ids) == 0 {
				continue
			}
			sort.Strings(ids)
			if kind == "TS" {
				n.submitFinalLote(ids, porID)
				continue
			}
			own, fab := juntaLote(ids, porID)
			for _, id := range ids {
				n.trace("DLV", id, TS{})
			}
			for i, r := range n.cfg.Chains[n.cfg.Chain].Replicas { // R-broadcast ao grupo (l. 30)
				if i == n.me {
					for _, id := range ids {
						n.app.onDLV(id, expandCerts(id, own, fab))
					}
				} else {
					n.send(r.Addr, Msg{Type: "DLVB", TxIDs: ids, Certs: own, Fab: fab})
				}
			}
		}
	}
}

// juntaLote: os certificados de um lote, os do Fabric sem repetição (um certificado do Fabric cobre várias tx).
func juntaLote(ids []string, porID map[string][]Cert) ([]Cert, []alg3Cert) {
	var own []Cert
	var fab []alg3Cert
	vistos := map[string]bool{}
	for _, id := range ids {
		for _, c := range porID[id] {
			if c.Fab == nil {
				own = append(own, c)
				continue
			}
			d := c.Fab.digest()
			k := c.Chain + string(d[:])
			if !vistos[k] {
				vistos[k] = true
				fab = append(fab, *c.Fab)
			}
		}
	}
	return own, fab
}

// maxFinalTx: teto da tx F| (hex) abaixo do max_tx_bytes padrão do mempool do CometBFT (1 MiB), com folga para o
// base64 do POST ficar abaixo do max_body_bytes (achado 6 do revisor: com vários certificados do Fabric de ~96 KB num
// lote, o FINAL_LOCAL seria recusado sempre).
var maxFinalTx = 700 * 1024

// submitFinalLote: FINAL_LOCAL em lote; acima do teto, divide o lote ao meio até caber (1 tx sozinha vai como está).
func (n *Net) submitFinalLote(ids []string, porID map[string][]Cert) {
	own, fab := juntaLote(ids, porID)
	b, _ := json.Marshal(finalMsg{IDs: ids, Certs: own, Fab: fab})
	if 2*len(b)+2 > maxFinalTx && len(ids) > 1 {
		n.submitFinalLote(ids[:len(ids)/2], porID)
		n.submitFinalLote(ids[len(ids)/2:], porID)
		return
	}
	for _, id := range ids {
		n.trace("FINAL_SUB", id, TS{})
	}
	n.submit("F|" + hex.EncodeToString(b))
}

// expandCerts: os certificados de uma tx dentro de um lote: os próprios (de 1 item) com o TxID dela e, de cada
// certificado do Fabric que tem um item dela, a visão desse item (TS: ⟨clock, grupo⟩; ACK: ⟨clock, nó⟩).
func expandCerts(id string, own []Cert, fab []alg3Cert) []Cert {
	var out []Cert
	for _, c := range own {
		if c.TxID == id {
			out = append(out, c)
		}
	}
	for i := range fab {
		f := &fab[i]
		for _, it := range f.Items {
			if it.TxID != id {
				continue
			}
			v := TS{it.Clock, f.Group}
			if f.Kind == "ACK" {
				v = TS{it.Clock, it.Node}
			}
			out = append(out, Cert{Kind: f.Kind, Chain: f.Group, TxID: id, Val: v, Fab: f})
			break
		}
	}
	return out
}

func haveAll(m map[string]*Cert, dest []string) bool {
	for _, g := range dest {
		if m[g] == nil {
			return false
		}
	}
	return true
}

// submitFinal: A-broadcast(FINAL_LOCAL, m, provas) no consenso do próprio grupo (l. 18) via RPC do CometBFT.
func (n *Net) submitFinal(id string, certs []Cert) {
	b, _ := json.Marshal(finalMsg{ID: id, Certs: certs})
	n.trace("FINAL_SUB", id, TS{})
	n.submit("F|" + hex.EncodeToString(b))
}

// submit: envia uma tx ao mempool do próprio consenso por POST JSON-RPC (16/11/2026: o FINAL_LOCAL carrega o certificado
// inteiro do Fabric, que cobre um lote; pela URL de um GET, as tx grandes não chegavam). Recusa do mempool (ex.: cheio)
// vai para o rastro como SUB_ERR; "já está no cache" é aceita.
func (n *Net) submit(tx string) {
	go func() {
		body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "broadcast_tx_async",
			"params": map[string]string{"tx": base64.StdEncoding.EncodeToString([]byte(tx))}})
		r, err := http.Post(n.cfg.Chains[n.cfg.Chain].RPC, "application/json", bytes.NewReader(body))
		if err != nil {
			n.trace("SUB_ERR", "", TS{Clock: uint64(len(tx))})
			return
		}
		var resp struct {
			Error *struct {
				Data string `json:"data"`
			} `json:"error"`
		}
		_ = json.NewDecoder(r.Body).Decode(&resp)
		r.Body.Close()
		if resp.Error != nil && !strings.Contains(resp.Error.Data, "already exists in cache") {
			n.trace("SUB_ERR", resp.Error.Data, TS{Clock: uint64(len(tx))})
		}
	}()
}

// checkTSCerts (FinalizeBlock, determinístico): um certificado TS válido de cada destino, o do próprio grupo igual
// ao local[m]; devolve final[m] = máximo (C1). Os destinos vêm do START aplicado, não do certificado.
func (n *Net) checkTSCerts(x *gtx, certs []Cert) (TS, bool) {
	por := map[string]Cert{}
	for _, c := range certs {
		if c.Kind == "TS" && c.TxID == x.id && c.Val.Chain == c.Chain && contains(x.dest, c.Chain) && n.verifyCert(c) {
			por[c.Chain] = c
		}
	}
	var fin TS
	for _, g := range x.dest {
		c, ok := por[g]
		if !ok {
			return TS{}, false
		}
		if g == x.local.Chain && c.Val != x.local {
			return TS{}, false
		}
		if fin.Less(c.Val) {
			fin = c.Val
		}
	}
	return fin, true
}

// ackCertOK: certificado ACK válido para a tx (o valor é conferido contra o final na hora de marcar acked).
func (n *Net) ackCertOK(id string, c Cert) bool {
	return c.Kind == "ACK" && c.TxID == id && n.verifyCert(c)
}

// ---------- recuperação por pedido (NEED) ----------

// retransmit: a cada segundo, para cada tx pendente aqui há mais de 1 s, pede o que falta (ver o cabeçalho).
func (n *Net) retransmit() {
	for range time.Tick(time.Second) {
		fabNeed := map[[2]string][]string{} // (grupo Fabric, tipo) → tx: um NEED agrupado por segundo, não um por tx
		for _, x := range n.app.pendingSnapshot() {
			n.mu.Lock()
			velho := time.Since(n.startAt[x.id]) > time.Second && time.Since(n.needAt[x.id]) > time.Second
			if velho {
				n.needAt[x.id] = time.Now()
			}
			ts, acks := n.certs["TS|"+x.id], n.certs["ACK|"+x.id]
			temTS, temACK := n.myCerts["TS|"+x.id] != nil, n.myCerts["ACK|"+x.id] != nil
			n.mu.Unlock()
			if !velho {
				continue
			}
			if !n.coordinator() {
				if x.finalized && !x.acked {
					n.send(n.coordAddr(n.cfg.Chain), Msg{Type: "NEED", Kind: "DLV", TxID: x.id, From: n.cfg.Chain, Rep: n.me})
				}
				continue
			}
			n.progress(x.id) // FINAL_LOCAL e DELIVER_LOCAL que ficaram para trás
			kind, have, mine := "TS", ts, temTS
			if x.finalized {
				kind, have, mine = "ACK", acks, temACK
			}
			start := "S|" + clientID(x.id) + "|" + strings.Join(x.dest, ",")
			if x.env != "" {
				start += "|" + x.env
			}
			for _, g := range x.dest {
				switch {
				case g == n.cfg.Chain || have[g] != nil:
				case n.isFabric(g): // NEED no formato do Fabric, agrupado (enviado no fim da volta)
					fabNeed[[2]string{g, kind}] = append(fabNeed[[2]string{g, kind}], x.id)
					// relay do START ao Fabric (Alg. 3, l. 5; o mesmo que o Fabric faz entre shards): sem o TS do
					// destino Fabric há alg3RelayStartAfter (3 s), reenvia o envelope assinado pelo cliente; o
					// coordenador do Fabric o ordena e cada réplica confere a assinatura do cliente (alg3InnerStart).
					// Sem isso uma global que chegou só ao CometBFT travava a blockchain (achado 3 do revisor).
					if kind == "TS" && x.env != "" {
						n.mu.Lock()
						vai := time.Since(n.startAt[x.id]) > 3*time.Second && time.Since(n.relayAt["out:"+g+"|"+x.id]) > 3*time.Second
						if vai {
							n.relayAt["out:"+g+"|"+x.id] = time.Now()
						}
						n.mu.Unlock()
						if env, err := base64.StdEncoding.DecodeString(x.env); vai && err == nil {
							n.trace("RELAY_FAB", x.id, TS{})
							for _, a := range n.cfg.Fabric[g].Addrs { // só o coordenador do Fabric ordena; os outros ignoram
								n.sendWire(a, alg3Msg{Type: "START", From: n.cfg.Chain, Env: env, Src: n.me + 1})
							}
						}
					}
				default:
					n.send(n.coordAddr(g), Msg{Type: "NEED", Kind: kind, TxID: x.id, From: n.cfg.Chain, Rep: n.me, Start: start})
				}
			}
			if !mine {
				for i, r := range n.cfg.Chains[n.cfg.Chain].Replicas {
					if i != n.me {
						n.send(r.Addr, Msg{Type: "NEED", Kind: "VOTE_" + kind, TxID: x.id, From: n.cfg.Chain, Rep: n.me})
					}
				}
			}
		}
		// NEED ao Fabric: a todas as réplicas (o coordenador do Fabric, líder do SmartBFT, pode ter mudado), em pedaços
		// de 500 tx; ele responde com REPLY ao alg3Addr deste grupo, réplica Rep
		for gk, ids := range fabNeed {
			for i := 0; i < len(ids); i += 500 {
				e := i + 500
				if e > len(ids) {
					e = len(ids)
				}
				for _, a := range n.cfg.Fabric[gk[0]].Addrs {
					n.sendWire(a, alg3Msg{Type: "NEED", Kind: gk[1], TxIDs: ids[i:e], From: n.cfg.Chain, Rep: n.me + 1})
				}
			}
		}
	}
}

// onNeed: responde com o que está guardado, qualquer que seja o estado desta réplica.
func (n *Net) onNeed(m Msg) {
	cc, ok := n.cfg.Chains[m.From]
	if !ok || m.Rep < 0 || m.Rep >= len(cc.Replicas) {
		return
	}
	switch m.Kind {
	case "TS", "ACK": // coordenador de outro destino pede o certificado deste grupo
		// só a destinos da própria tx (achado R2 do revisor): os destinos estão no gid, então dá para conferir
		// mesmo antes de este nó aplicar o START
		if d := destOf(m.TxID); !n.coordinator() || m.From == n.cfg.Chain || !contains(d, m.From) || !contains(d, n.cfg.Chain) {
			return
		}
		n.mu.Lock()
		c := n.myCerts[m.Kind+"|"+m.TxID]
		n.mu.Unlock()
		if c != nil {
			n.send(cc.Replicas[m.Rep].Addr, Msg{Type: "CERT", Cert: c})
			return
		}
		if _, conhece := n.app.lookup(m.TxID); m.Kind == "TS" && !conhece { // relay do START (l. 5)
			p, okp := parseTx([]byte(m.Start))
			if !okp || p.kind != "S" || p.id != m.TxID || !n.startOK(p) || !contains(p.dest, m.From) {
				return
			}
			n.mu.Lock()
			vai := time.Since(n.relayAt[m.TxID]) > 3*time.Second
			if vai {
				n.relayAt[m.TxID] = time.Now()
			}
			n.mu.Unlock()
			if vai {
				n.trace("RELAY", m.TxID, TS{})
				n.submit(m.Start)
			}
		}
	case "VOTE_TS", "VOTE_ACK": // coordenador do próprio grupo pede o voto
		if m.From != n.cfg.Chain || m.Rep != 0 {
			return
		}
		n.mu.Lock()
		v, okv := n.myVotes[strings.TrimPrefix(m.Kind, "VOTE_")+"|"+m.TxID]
		n.mu.Unlock()
		if okv {
			n.sendVote(v)
		}
	case "DLV": // réplica do próprio grupo pede o DELIVER_LOCAL
		if !n.coordinator() || m.From != n.cfg.Chain {
			return
		}
		x, conhece := n.app.lookup(m.TxID)
		n.mu.Lock()
		acks := n.certs["ACK|"+m.TxID]
		var certs []Cert
		if conhece && haveAll(acks, x.dest) {
			for _, g := range x.dest {
				certs = append(certs, *acks[g])
			}
		}
		n.mu.Unlock()
		if certs != nil {
			n.send(cc.Replicas[m.Rep].Addr, Msg{Type: "DLV", TxID: m.TxID, Certs: certs})
		}
	}
}

// ---------- transporte ----------

func (n *Net) send(addr string, m Msg) {
	if n.drop > 0 && rand.Float64() < n.drop { // perda injetada (teste)
		return
	}
	n.mu.Lock()
	q := n.senders[addr]
	if q == nil {
		q = make(chan Msg, 65536)
		n.senders[addr] = q
		go senderLoop(addr, q)
	}
	n.mu.Unlock()
	select {
	case q <- m:
	default: // fila cheia: descarta; o NEED de quem espera repõe
	}
}

func senderLoop(addr string, q chan Msg) {
	var conn net.Conn
	var w *bufio.Writer
	var enc *json.Encoder
	for m := range q {
		for try := 0; try < 2; try++ {
			if conn == nil {
				c, err := net.DialTimeout("tcp", addr, time.Second)
				if err != nil {
					time.Sleep(100 * time.Millisecond)
					continue
				}
				conn, w = c, bufio.NewWriterSize(c, 256<<10)
				enc = json.NewEncoder(w)
			}
			err := enc.Encode(m)
			if err == nil && len(q) == 0 {
				err = w.Flush()
			}
			if err != nil {
				conn.Close()
				conn = nil
				continue
			}
			break
		}
	}
}

func (n *Net) listen() error {
	ln, err := net.Listen("tcp", n.cfg.Listen)
	if err != nil {
		return err
	}
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				continue
			}
			go func(c net.Conn) {
				defer c.Close()
				dec := json.NewDecoder(bufio.NewReaderSize(c, 256<<10))
				for {
					var m Msg
					if dec.Decode(&m) != nil {
						return
					}
					n.handle(m)
				}
			}(c)
		}
	}()
	return nil
}

func (n *Net) handle(m Msg) {
	switch m.Type {
	case "VOTE":
		if m.Vote != nil {
			n.onVote(*m.Vote)
		}
	case "CERT":
		if m.Cert != nil {
			n.onCert(*m.Cert)
		}
	case "DLV":
		n.app.onDLV(m.TxID, m.Certs)
	case "DLVB":
		for _, id := range m.TxIDs {
			n.app.onDLV(id, expandCerts(id, m.Certs, m.Fab))
		}
	case "NEED":
		n.onNeed(m)
	}
}
