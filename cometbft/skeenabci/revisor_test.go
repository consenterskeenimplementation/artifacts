package main

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"testing"

	abci "github.com/cometbft/cometbft/abci/types"
	"github.com/cometbft/cometbft/crypto"
	"github.com/cometbft/cometbft/crypto/ed25519"
	cmtlog "github.com/cometbft/cometbft/libs/log"
)

type rig struct {
	keys map[string][]crypto.PrivKey
	cfg  Config
}

func newRig() *rig {
	r := &rig{keys: map[string][]crypto.PrivKey{}, cfg: Config{F: 1, Chains: map[string]ChainConfig{}}}
	for _, g := range []string{"A", "B"} {
		var reps []Replica
		for i := 0; i < 4; i++ {
			k := ed25519.GenPrivKey()
			r.keys[g] = append(r.keys[g], k)
			reps = append(reps, Replica{Addr: "127.0.0.1:1", PubKey: base64.StdEncoding.EncodeToString(k.PubKey().Bytes())})
		}
		r.cfg.Chains[g] = ChainConfig{Replicas: reps, RPC: "http://127.0.0.1:1"}
	}
	return r
}

func (r *rig) replica(g string, i int) *SkeenApp {
	c := r.cfg
	c.Chain = g
	n, err := NewNet(c, r.keys[g][i], "")
	if err != nil {
		panic(err)
	}
	a := NewSkeenApp(g, cmtlog.NewNopLogger())
	a.net, n.app = n, a
	return a
}

func (r *rig) cert(kind, g, tx string, v TS) Cert {
	c := Cert{Kind: kind, Chain: g, TxID: tx, Val: v, Sigs: map[int][]byte{}}
	for i := 0; i < 2; i++ {
		s, _ := r.keys[g][i].Sign(signBytes(kind, g, tx, v))
		c.Sigs[i] = s
	}
	return c
}

func fb(a *SkeenApp, txs ...string) {
	var b [][]byte
	for _, t := range txs {
		b = append(b, []byte(t))
	}
	a.FinalizeBlock(context.Background(), &abci.RequestFinalizeBlock{Txs: b})
}

// Achados do revisor de fidelidade (16/11/2026), agora exigindo o comportamento corrigido.

// X: identidade da tx global "x" com destinos A e B (id do cliente + destinos, achado R1)
var X = gid("x", []string{"A", "B"})

// B2: START com destino desconhecido é recusado na entrada e ignorado no FinalizeBlock; as locais seguem entregues.
func TestDestinoDesconhecidoRecusado(t *testing.T) {
	r := newRig()
	a := r.replica("A", 1)
	for _, tx := range []string{"S|x|A,Z", "S|x|A,A", "S|x|B,C"} {
		if res, _ := a.CheckTx(context.Background(), &abci.RequestCheckTx{Tx: []byte(tx)}); res.Code == 0 {
			t.Fatalf("CheckTx aceitou %s", tx)
		}
	}
	fb(a, "S|x|A,Z")
	fb(a, "L|y1", "L|y2")
	if a.seq != 2 {
		t.Fatalf("entregues=%d, esperado 2", a.seq)
	}
}

// B3: um DELIVER_LOCAL inválido depois do válido não apaga o válido.
func TestPendDLVNaoSobrescrito(t *testing.T) {
	r := newRig()
	a := r.replica("A", 1)
	fb(a, "S|x|A,B")
	loc := a.txs[X].local
	fin := TS{5, "B"}
	a.onDLV(X, []Cert{r.cert("ACK", "A", X, fin), r.cert("ACK", "B", X, fin)})
	a.onDLV(X, nil)
	lixo := r.cert("ACK", "B", X, fin)
	lixo.Sigs[0] = []byte("lixo")
	lixo.Sigs[1] = []byte("lixo")
	a.onDLV(X, []Cert{lixo})
	fb(a, finalTx(r.cert("TS", "A", X, loc), r.cert("TS", "B", X, fin)))
	fb(a, "L|y")
	if !a.txs[X].acked || a.seq != 2 {
		t.Fatalf("acked=%v entregues=%d, esperado true e 2", a.txs[X].acked, a.seq)
	}
}

// B4: um id usado por uma local e por uma global não colide (espaços de id separados).
func TestColisaoLSSeparada(t *testing.T) {
	r := newRig()
	b := r.replica("B", 1)
	fb(b, "L|x")
	fb(b, "S|x|A,B")
	if x := b.txs[X]; x == nil || !x.global {
		t.Fatal("START de x ignorado depois da local x")
	}
	if b.txs["L|x"] == nil {
		t.Fatal("local x sumiu")
	}
}

// C3: ACK sobre outro final é recusado; ACK sobre o final certo marca acked.
func TestACKVinculaFinal(t *testing.T) {
	r := newRig()
	a := r.replica("A", 1)
	fb(a, "S|x|A,B")
	loc := a.txs[X].local
	fb(a, finalTx(r.cert("TS", "A", X, loc), r.cert("TS", "B", X, TS{5, "B"})))
	a.onDLV(X, []Cert{r.cert("ACK", "A", X, TS{6, "B"}), r.cert("ACK", "B", X, TS{6, "B"})})
	if a.txs[X].acked {
		t.Fatal("aceitou ACK de outro final")
	}
	a.onDLV(X, []Cert{r.cert("ACK", "A", X, TS{5, "B"}), r.cert("ACK", "B", X, TS{5, "B"})})
	if !a.txs[X].acked {
		t.Fatal("não aceitou o ACK do final certo")
	}
}

// Certificado com f assinaturas (1 de 4) é recusado; com f+1 (2), aceito.
func TestCertificadoPrecisaFmais1(t *testing.T) {
	r := newRig()
	a := r.replica("A", 1)
	c := r.cert("TS", "B", X, TS{3, "B"})
	if !a.net.verifyCert(c) {
		t.Fatal("recusou certificado com f+1")
	}
	delete(c.Sigs, 1)
	if a.net.verifyCert(c) {
		t.Fatal("aceitou certificado com f assinaturas")
	}
}

func finalTx(certs ...Cert) string {
	b, _ := json.Marshal(finalMsg{ID: X, Certs: certs})
	return "F|" + hex.EncodeToString(b)
}

// R1: o mesmo id com destinos diferentes são mensagens distintas (cada uma com o seu final), em vez de travar.
func TestMesmoIdDestinosDiferentes(t *testing.T) {
	r := newRig()
	a := r.replica("A", 1)
	fb(a, "S|x|A,B", "S|x|B,A") // mesma mensagem (destinos em outra ordem): uma só
	if len(a.txs) != 1 {
		t.Fatalf("esperado 1 tx, há %d", len(a.txs))
	}
	if a.txs[X] == nil {
		t.Fatal("identidade x@A,B ausente")
	}
}
