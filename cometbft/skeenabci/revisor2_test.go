package main

// Testes da reconferência do revisor de 17/11/2026 (achados 1, 2, 3 e 9; incorporados dos testes de reprodução dele).

import (
	"context"
	"crypto/ecdsa"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sync"
	"testing"
	"time"

	abci "github.com/cometbft/cometbft/abci/types"
	"github.com/cometbft/cometbft/crypto"
	"github.com/cometbft/cometbft/crypto/ed25519"
	cmtlog "github.com/cometbft/cometbft/libs/log"
	"google.golang.org/protobuf/encoding/protowire"
)

type hrig struct {
	cmtKeys []crypto.PrivKey
	fabKeys []*ecdsa.PrivateKey
	fabIDs  [][]byte
	cfg     Config
}

func newHrig(t *testing.T) *hrig {
	h := &hrig{cfg: Config{Chain: "canal2", Chains: map[string]ChainConfig{}, Fabric: map[string]FabricGroup{}}}
	var reps []Replica
	for i := 0; i < 4; i++ {
		k := ed25519.GenPrivKey()
		h.cmtKeys = append(h.cmtKeys, k)
		reps = append(reps, Replica{Addr: "127.0.0.1:1", PubKey: base64.StdEncoding.EncodeToString(k.PubKey().Bytes())})
	}
	h.cfg.Chains["canal2"] = ChainConfig{Replicas: reps, RPC: "http://127.0.0.1:1"}
	var pems, addrs []string
	for i := 0; i < 4; i++ {
		k, p, id := ordererTeste(t)
		h.fabKeys, h.fabIDs, pems = append(h.fabKeys, k), append(h.fabIDs, id), append(pems, p)
		addrs = append(addrs, "127.0.0.1:1")
	}
	h.cfg.Fabric["canal1"] = FabricGroup{Addrs: addrs, PEMs: pems}
	return h
}

func (h *hrig) node(i int) *SkeenApp {
	n, err := NewNet(h.cfg, h.cmtKeys[i], "")
	if err != nil {
		panic(err)
	}
	a := NewSkeenApp("canal2", cmtlog.NewNopLogger())
	a.net, n.app = n, a
	return a
}

func (h *hrig) fabCert(kind string, items []alg3Item, signers ...int) alg3Cert {
	ct := alg3Cert{Kind: kind, Group: "canal1", Seq: 9, Items: items}
	d := ct.digest()
	hh := sha256.Sum256(d[:])
	for _, i := range signers {
		s, _ := ecdsa.SignASN1(rand.Reader, h.fabKeys[i], hh[:])
		ct.Sigs = append(ct.Sigs, alg3Sig{ID: h.fabIDs[i], Sig: s})
	}
	return ct
}

func (h *hrig) ownCert(kind, tx string, v TS) Cert {
	c := Cert{Kind: kind, Chain: "canal2", TxID: tx, Val: v, Sigs: map[int][]byte{}}
	for i := 0; i < 2; i++ {
		s, _ := h.cmtKeys[i].Sign(signBytes(kind, "canal2", tx, v))
		c.Sigs[i] = s
	}
	return c
}

func fbx(a *SkeenApp, txs ...string) {
	var b [][]byte
	for _, t := range txs {
		b = append(b, []byte(t))
	}
	a.FinalizeBlock(context.Background(), &abci.RequestFinalizeBlock{Txs: b})
}

// Achado 1: a chave do cache fabOK concatena ID||Sig sem delimitador; um certificado com os bytes "deslocados" entre ID e
// Sig tem a mesma chave e é aceito por quem já verificou o original (coordenador, via onCert) e recusado por quem não
// verificou: FinalizeBlock deixa de ser determinístico.
func TestCacheFabOKDeterministico(t *testing.T) {
	h := newHrig(t)
	coord, rep := h.node(0), h.node(1)
	id := "CROSS_canal1-canal2_z"
	start := sFab(id)
	fbx(coord, start)
	fbx(rep, start)
	fab := h.fabCert("TS", []alg3Item{{TxID: id, Clock: 7, Dest: []string{"canal1", "canal2"}}}, 0, 1)
	// o coordenador recebeu o CERT do Fabric pelo fio (verifica e guarda no cache)
	coord.net.handleWire(alg3Msg{Type: "CERT", From: "canal1", Certs: []alg3Cert{fab}, Src: 1})
	// versão adulterada: último byte do ID 0 passa para o começo da assinatura 0 (mesma concatenação)
	bad := fab
	bad.Sigs = append([]alg3Sig(nil), fab.Sigs...)
	id0 := bad.Sigs[0].ID
	bad.Sigs[0] = alg3Sig{ID: append([]byte(nil), id0[:len(id0)-1]...), Sig: append([]byte{id0[len(id0)-1]}, fab.Sigs[0].Sig...)}
	if verifyFabricCert(&bad, coord.net.fsigners["canal1"]) {
		t.Fatal("adulterado deveria ser inválido sem cache")
	}
	loc := coord.txs[id].local
	b, _ := json.Marshal(finalMsg{IDs: []string{id}, Certs: []Cert{h.ownCert("TS", id, loc)}, Fab: []alg3Cert{bad}})
	ftx := "F|" + hex.EncodeToString(b)
	fbx(coord, ftx)
	fbx(rep, ftx)
	t.Logf("coordenador finalized=%v; réplica 1 finalized=%v", coord.txs[id].finalized, rep.txs[id].finalized)
	if coord.txs[id].finalized || rep.txs[id].finalized {
		t.Errorf("certificado adulterado aceito (coord=%v, r1=%v)", coord.txs[id].finalized, rep.txs[id].finalized)
	}
	if coord.txs[id].finalized != rep.txs[id].finalized {
		t.Errorf("FinalizeBlock NÃO determinístico: mesmo bloco, decisões diferentes (coord=%v, r1=%v)",
			coord.txs[id].finalized, rep.txs[id].finalized)
	}
}


// fabEnvelope: um common.Envelope mínimo do Fabric com o TxID no ChannelHeader (campo 5), como o envelopeTxID lê.
func fabEnvelope(txID string) []byte {
	ch := protowire.AppendTag(nil, 5, protowire.BytesType)
	ch = protowire.AppendString(ch, txID)
	hd := protowire.AppendTag(nil, 1, protowire.BytesType)
	hd = protowire.AppendBytes(hd, ch)
	pl := protowire.AppendTag(nil, 1, protowire.BytesType)
	pl = protowire.AppendBytes(pl, hd)
	env := protowire.AppendTag(nil, 1, protowire.BytesType)
	return protowire.AppendBytes(env, pl)
}

// sFab: START de uma global canal1 (Fabric) + canal2 (CometBFT), com o envelope do cliente, como o cliente_e2e envia.
func sFab(id string) string {
	return "S|" + id + "|canal1,canal2|" + base64.StdEncoding.EncodeToString(fabEnvelope(id))
}

// Reconferência (resíduo do achado 3): START sem envelope para uma global com destino Fabric é recusado, no CheckTx e
// no FinalizeBlock, em todas as réplicas; senão ninguém o levaria ao Fabric e a blockchain travaria.
func TestStartSemEnvelopeRecusado(t *testing.T) {
	h := newHrig(t)
	a := h.node(1)
	id := "CROSS_canal1-canal2_semenv"
	r, _ := a.CheckTx(context.Background(), &abci.RequestCheckTx{Tx: []byte("S|" + id + "|canal1,canal2")})
	if r.Code == 0 {
		t.Fatal("CheckTx aceitou START sem envelope com destino Fabric")
	}
	fbx(a, "S|"+id+"|canal1,canal2", "L|depois")
	if _, conhece := a.lookup(id); conhece {
		t.Fatal("FinalizeBlock aplicou START sem envelope")
	}
	if a.seq != 1 {
		t.Fatalf("a local seguinte deveria ser entregue (seq=%d)", a.seq)
	}
	fbx(a, sFab(id)) // a versão com envelope (a do cliente honesto) continua valendo
	if _, conhece := a.lookup(id); !conhece {
		t.Fatal("START com envelope recusado depois da versão sem envelope")
	}
}

// Achado 2: o que o Fabric aceita como cross canal1+canal2 o CometBFT também aceita, e vice-versa (o Fabric agora recusa
// '|', '@' e espaço; ver TestParseDestParidadeCometBFT em skeen_alg3/chain_hosts_test.go).
func TestParidadeParsingFabric(t *testing.T) {
	h := newHrig(t)
	a := h.node(0)
	if p, ok := parseTx([]byte("S|CROSS_canal1-canal2|canal1,canal2")); !ok || !a.net.validDest(p.dest) {
		t.Fatal("CROSS_ sem sufixo recusado (o Fabric aceita)")
	}
	for _, id := range []string{"CROSS_canal1-canal2_a@b", "CROSS_canal1-canal2_a b"} {
		if _, ok := parseTx([]byte("S|" + id + "|canal1,canal2")); ok {
			t.Fatalf("%q aceito (o Fabric recusa)", id)
		}
	}
}

// Achado 3: o START pode levar o envelope do cliente para o Fabric; ele só vale se for o da própria tx, e fica guardado
// na tx até a entrega (o coordenador o reenvia ao Fabric sem TS de lá).
func TestStartComEnvelope(t *testing.T) {
	h := newHrig(t)
	a := h.node(1)
	id := "CROSS_canal1-canal2_env"
	ok64 := base64.StdEncoding.EncodeToString(fabEnvelope(id))
	outro := base64.StdEncoding.EncodeToString(fabEnvelope("CROSS_canal1-canal2_outra"))
	if _, ok := parseTx([]byte("S|" + id + "|canal1,canal2|" + outro)); ok {
		t.Fatal("envelope de outra tx aceito")
	}
	if _, ok := parseTx([]byte("S|" + id + "|canal1,canal2|nao-e-base64!")); ok {
		t.Fatal("envelope inválido aceito")
	}
	if _, ok := parseTx([]byte("S|semcross|canal1,canal2|" + ok64)); ok {
		t.Fatal("envelope em id que não é do Fabric aceito")
	}
	fbx(a, "S|"+id+"|canal1,canal2|"+ok64)
	x, conhece := a.lookup(id)
	if !conhece || x.env != ok64 {
		t.Fatalf("START com envelope não guardou o envelope: %v %q", conhece, x.env)
	}
	if ps := a.pendingSnapshot(); len(ps) != 1 || ps[0].id != id {
		t.Fatalf("pendentes: %v", ps)
	}
	// o mesmo START sem envelope (vindo do cliente pela RPC) é a mesma tx: não duplica
	fbx(a, "S|"+id+"|canal1,canal2")
	if len(a.pendingSnapshot()) != 1 {
		t.Fatal("START repetido criou outra tx")
	}
}

// Achado 9: depois da entrega a tx sai do índice de pendentes (a retransmissão não percorre as entregues).
func TestPendentesSaemNaEntrega(t *testing.T) {
	h := newHrig(t)
	a := h.node(1)
	id := "CROSS_canal1-canal2_p"
	fbx(a, sFab(id))
	x, _ := a.lookup(id)
	fab := h.fabCert("TS", []alg3Item{{TxID: id, Clock: 5, Dest: []string{"canal1", "canal2"}}}, 0, 1)
	b, _ := json.Marshal(finalMsg{IDs: []string{id}, Certs: []Cert{h.ownCert("TS", id, x.local)}, Fab: []alg3Cert{fab}})
	fbx(a, "F|"+hex.EncodeToString(b))
	x, _ = a.lookup(id)
	if !x.finalized {
		t.Fatal("não finalizou")
	}
	ack := h.fabCert("ACK", []alg3Item{{TxID: id, Clock: x.final.Clock, Node: x.final.Chain, Dest: []string{"canal1", "canal2"}}}, 0, 1)
	a.onDLV(id, append(expandCerts(id, nil, []alg3Cert{ack}), h.ownCert("ACK", id, x.final)))
	if x, _ = a.lookup(id); !x.delivered {
		t.Fatal("não entregou")
	}
	if n := len(a.pendingSnapshot()); n != 0 {
		t.Fatalf("%d pendentes depois da entrega", n)
	}
}

// Concorrência: 8 trabalhadores do fio + FinalizeBlock + DLVB + batchLoop + retransmit, para o -race.
func TestConcorrenciaWire(t *testing.T) {
	h := newHrig(t)
	coord := h.node(0)
	coord.net.wireIn = make(chan alg3Msg, 1<<16)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for m := range coord.net.wireIn {
				coord.net.handleWire(m)
			}
		}()
	}
	go coord.net.batchLoop()
	go coord.net.retransmit()
	const N = 200
	ids := make([]string, N)
	for i := range ids {
		ids[i] = fmt.Sprintf("CROSS_canal1-canal2_t%d", i)
	}
	// certificados TS do Fabric em lotes de 20, chegando ANTES e DEPOIS do START, em paralelo
	var items []alg3Item
	for i, id := range ids {
		items = append(items, alg3Item{TxID: id, Clock: uint64(1000 + i), Dest: []string{"canal1", "canal2"}})
	}
	var fabTS []alg3Cert
	for i := 0; i < N; i += 20 {
		fabTS = append(fabTS, h.fabCert("TS", items[i:i+20], 0, 2))
	}
	go func() {
		for _, c := range fabTS {
			coord.net.wireIn <- alg3Msg{Type: "CERT", From: "canal1", Certs: []alg3Cert{c}, Src: 1}
			coord.net.wireIn <- alg3Msg{Type: "NEED", From: "canal1", Kind: "TS", TxIDs: ids, Rep: 2}
		}
	}()
	for i := 0; i < N; i += 10 {
		var blk []string
		for _, id := range ids[i : i+10] {
			blk = append(blk, sFab(id), "L|l"+id)
		}
		fbx(coord, blk...)
		// votos TS das réplicas 1 (o coordenador já votou) → certificado próprio
		for _, id := range ids[i : i+10] {
			x, _ := coord.lookup(id)
			s, _ := h.cmtKeys[1].Sign(signBytes("TS", "canal2", id, x.local))
			coord.net.onVote(Vote{Kind: "TS", Chain: "canal2", TxID: id, Val: x.local, Replica: 1, Sig: s})
		}
	}
	time.Sleep(300 * time.Millisecond)
	// FINAL em lote como o batchLoop montaria (aplicado direto, sem RPC)
	var own []Cert
	for _, id := range ids {
		x, _ := coord.lookup(id)
		own = append(own, h.ownCert("TS", id, x.local))
	}
	b, _ := json.Marshal(finalMsg{IDs: ids, Certs: own, Fab: fabTS})
	fbx(coord, "F|"+hex.EncodeToString(b))
	fin := 0
	for _, id := range ids {
		if x, _ := coord.lookup(id); x.finalized {
			fin++
		}
	}
	if fin != N {
		t.Errorf("finalizadas %d/%d", fin, N)
	}
	time.Sleep(1200 * time.Millisecond)
	close(coord.net.wireIn)
	wg.Wait()
}
