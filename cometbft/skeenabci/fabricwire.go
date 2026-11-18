package main

// Fio do Fabric (16/11/2026, Skeen entre plataformas Fabric × CometBFT; cometbft/PLANO_HETERO.md). A blockchain CometBFT
// se apresenta ao Fabric como um grupo "canalN" e troca com o coordenador do Fabric as mesmas mensagens do consenter
// Skeen (skeen_alg3/chain_alg3.go): alg3Msg e alg3Cert em msgpack, com o mesmo resumo do certificado. O resumo precisa
// sair idêntico byte a byte nos dois lados: TestFabricDigestVetor confere o vetor gerado pelo código do Fabric
// (TestAlg3DigestVetor em skeen_alg3/chain_hosts_test.go).
//
// Assinaturas: o CometBFT assina o resumo com ed25519 (Sig.ID = "ed25519:<i>", que o Fabric verifica com SKEEN_FOREIGN);
// o Fabric assina com a identidade MSP do orderer (ECDSA P-256 sobre SHA-256 do resumo), que o CometBFT verifica com os
// certificados x509 dos orderers fixados na configuração.
import (
	"bufio"
	"crypto/ecdsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/binary"
	"encoding/pem"
	"fmt"
	"math/rand"
	"net"
	"strconv"
	"strings"
	"time"

	"github.com/vmihailenco/msgpack/v5"
	"google.golang.org/protobuf/encoding/protowire"
)

// Tipos idênticos aos do Fabric (mesmos nomes de campo e tags msgpack).
type alg3Item struct {
	TxID  string   `msgpack:"x"`
	Clock uint64   `msgpack:"c,omitempty"`
	Node  string   `msgpack:"n,omitempty"`
	Dest  []string `msgpack:"d"`
}

type alg3Sig struct {
	ID  []byte `msgpack:"i"`
	Sig []byte `msgpack:"s,omitempty"`
}

type alg3Cert struct {
	Kind  string     `msgpack:"k"`
	Group string     `msgpack:"g"`
	Seq   uint64     `msgpack:"q"`
	Items []alg3Item `msgpack:"it"`
	Sigs  []alg3Sig  `msgpack:"s"`
}

type alg3Msg struct {
	Type  string     `msgpack:"t"`
	From  string     `msgpack:"f"`
	Certs []alg3Cert `msgpack:"c,omitempty"`
	Env   []byte     `msgpack:"e,omitempty"`
	Kind  string     `msgpack:"k,omitempty"`
	TxIDs []string   `msgpack:"x,omitempty"`
	Rep   int        `msgpack:"r,omitempty"`
	Src   int        `msgpack:"o,omitempty"`
}

// digest: SHA-256 do msgpack de {K, G, Q, I} (alg3Cert.computeDigest do Fabric).
func (ct *alg3Cert) digest() [32]byte {
	b, _ := msgpack.Marshal(struct {
		K string
		G string
		Q uint64
		I []alg3Item
	}{ct.Kind, ct.Group, ct.Seq, ct.Items})
	return sha256.Sum256(b)
}

// signFabricWire: assinatura desta réplica CometBFT sobre o resumo, no formato aceito pelo Fabric.
func signFabricWire(n *Net, ct *alg3Cert) alg3Sig {
	d := ct.digest()
	s, err := n.priv.Sign(d[:])
	if err != nil {
		panic(err)
	}
	return alg3Sig{ID: []byte("ed25519:" + strconv.Itoa(n.me)), Sig: s}
}

// fabricSigner: um orderer do Fabric, pelo certificado x509 fixado na configuração.
type fabricSigner struct {
	pem []byte
	pub *ecdsa.PublicKey
}

// parseFabricSigners: certificados PEM dos orderers de um grupo Fabric.
func parseFabricSigners(pems []string) ([]fabricSigner, error) {
	var out []fabricSigner
	for i, p := range pems {
		blk, _ := pem.Decode([]byte(p))
		if blk == nil {
			return nil, fmt.Errorf("certificado %d sem PEM", i)
		}
		c, err := x509.ParseCertificate(blk.Bytes)
		if err != nil {
			return nil, fmt.Errorf("certificado %d: %v", i, err)
		}
		pub, ok := c.PublicKey.(*ecdsa.PublicKey)
		if !ok {
			return nil, fmt.Errorf("certificado %d não é ECDSA", i)
		}
		out = append(out, fabricSigner{pem: []byte(strings.TrimSpace(p)), pub: pub})
	}
	return out, nil
}

// serializedIdentityPEM: id_bytes (campo 2) da msp.SerializedIdentity do Fabric (campo 1 = mspid).
func serializedIdentityPEM(raw []byte) []byte {
	for len(raw) > 0 {
		num, typ, n := protowire.ConsumeTag(raw)
		if n < 0 {
			return nil
		}
		raw = raw[n:]
		if typ != protowire.BytesType {
			return nil
		}
		v, m := protowire.ConsumeBytes(raw)
		if m < 0 {
			return nil
		}
		raw = raw[m:]
		if num == 2 {
			return []byte(strings.TrimSpace(string(v)))
		}
	}
	return nil
}

// verifyFabricCert: f+1 assinaturas ECDSA válidas de orderers distintos (fixados) do grupo Fabric sobre o resumo, com
// f = (n−1)/3 do grupo.
func verifyFabricCert(ct *alg3Cert, signers []fabricSigner) bool {
	d := ct.digest()
	h := sha256.Sum256(d[:]) // a identidade MSP do Fabric assina SHA-256(mensagem); a mensagem é o resumo
	distinct := map[int]bool{}
	for _, s := range ct.Sigs {
		p := serializedIdentityPEM(s.ID)
		for i, f := range signers {
			if distinct[i] || string(p) != string(f.pem) {
				continue
			}
			if !ecdsa.VerifyASN1(f.pub, h[:], s.Sig) {
				return false
			}
			distinct[i] = true
		}
	}
	return len(signers) > 0 && len(distinct) >= (len(signers)-1)/3+1
}

// ---------- ponte com o Fabric (coordenador CometBFT ↔ coordenador do Fabric) ----------

// FabricGroup: um grupo (canal) do Fabric que é destino de tx globais junto com esta blockchain.
type FabricGroup struct {
	Addrs []string `json:"addrs"` // alg3Addr de cada réplica r = 1..n (IP do canal, porta gRPC + 20000)
	PEMs  []string `json:"pems"`  // certificado x509 (PEM) do orderer de cada réplica, para verificar as assinaturas
}

func (n *Net) isFabric(g string) bool { _, ok := n.cfg.Fabric[g]; return ok }

// verifyFabricView: um Cert que é a visão de uma tx dentro de um certificado do Fabric: o certificado inteiro é válido
// (f+1 orderers distintos e fixados) e tem o item da tx com o valor declarado (TS: ⟨clock, grupo⟩; ACK: ⟨clock, nó⟩).
func (n *Net) verifyFabricView(c Cert) bool {
	f := c.Fab
	if f == nil || f.Group != c.Chain || f.Kind != c.Kind {
		return false
	}
	ok := false
	for _, it := range f.Items {
		if it.TxID != c.TxID {
			continue
		}
		v := TS{it.Clock, f.Group}
		if c.Kind == "ACK" {
			v = TS{it.Clock, it.Node}
		}
		ok = v == c.Val
		break
	}
	if !ok {
		return false
	}
	key := certCacheKey(f.digest(), f.Sigs)
	if _, hit := n.fabOK.Load(key); hit {
		return true
	}
	if !verifyFabricCert(f, n.fsigners[c.Chain]) {
		return false
	}
	n.fabOK.Store(key, true)
	return true
}

// certCacheKey: chave do cache de certificados do Fabric já verificados: resumo + cada (ID, Sig) com prefixo de tamanho.
// Sem o prefixo, um byte movido da fronteira ID|Sig dava a mesma chave e o coordenador (que já tinha verificado o
// original pelo fio) aceitava no FinalizeBlock um certificado adulterado que as outras réplicas recusavam (achado 1 do
// revisor, 17/11/2026: decisão não determinística). Igual a alg3CertKey do Fabric.
func certCacheKey(d [32]byte, sigs []alg3Sig) string {
	b := append(make([]byte, 0, 32+len(sigs)*120), d[:]...)
	for _, s := range sigs {
		b = binary.AppendUvarint(b, uint64(len(s.ID)))
		b = append(b, s.ID...)
		b = binary.AppendUvarint(b, uint64(len(s.Sig)))
		b = append(b, s.Sig...)
	}
	return string(b)
}

// sendFab: mensagem do fio do Fabric ao coordenador do grupo g (pista aprendida do Src de um CERT do Fabric) ou, sem
// pista, a todas as réplicas dele (como o alg3SendCoord do Fabric).
func (n *Net) sendFab(g string, m alg3Msg) {
	fg := n.cfg.Fabric[g]
	n.mu.Lock()
	r := n.fabCoord[g]
	n.mu.Unlock()
	if r >= 1 && r <= len(fg.Addrs) {
		n.sendWire(fg.Addrs[r-1], m)
		return
	}
	for _, a := range fg.Addrs {
		n.sendWire(a, m)
	}
}

func (n *Net) sendWire(addr string, m alg3Msg) {
	if n.drop > 0 && rand.Float64() < n.drop {
		return
	}
	n.mu.Lock()
	q := n.wireQ[addr]
	if q == nil {
		q = make(chan alg3Msg, 65536)
		n.wireQ[addr] = q
		go wireSender(addr, q)
	}
	n.mu.Unlock()
	select {
	case q <- m:
	default:
	}
}

func wireSender(addr string, q chan alg3Msg) {
	var conn net.Conn
	var w *bufio.Writer
	var enc *msgpack.Encoder
	for m := range q {
		for try := 0; try < 2; try++ {
			if conn == nil {
				c, err := net.DialTimeout("tcp", addr, time.Second)
				if err != nil {
					time.Sleep(100 * time.Millisecond)
					continue
				}
				conn, w = c, bufio.NewWriterSize(c, 256<<10)
				enc = msgpack.NewEncoder(w)
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

// listenWire (coordenador): escuta o fio do Fabric. A leitura de cada conexão só decodifica e enfileira; 8 trabalhadores
// processam (como o inbox do Fabric). Antes, processar na própria goroutine da conexão travava a leitura quando a
// aplicação estava ocupada; o TCP enchia, a fila de envio do Fabric (que descarta quando cheia) perdia certificados ACK e,
// passado o alg3DoneTTL de 1 min do Fabric, a tx ficava presa no CometBFT (achado de 17/11/2026).
func (n *Net) listenWire() error {
	n.wireIn = make(chan alg3Msg, 1<<16)
	for i := 0; i < 8; i++ {
		go func() {
			for m := range n.wireIn {
				n.handleWire(m)
			}
		}()
	}
	for _, a := range n.cfg.WireListen {
		ln, err := net.Listen("tcp", a)
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
					dec := msgpack.NewDecoder(bufio.NewReaderSize(c, 256<<10))
					for {
						var m alg3Msg
						if err := dec.Decode(&m); err != nil {
							if err.Error() != "EOF" {
								n.trace("W_DECERR", err.Error(), TS{})
							}
							return
						}
						x := ""
						if len(m.TxIDs) > 0 {
							x = m.TxIDs[0]
						}
						n.trace("W_"+m.Type, x, TS{Clock: uint64(len(m.Certs) + len(m.TxIDs))})
						n.wireIn <- m
					}
				}(c)
			}
		}()
	}
	return nil
}

func (n *Net) handleWire(m alg3Msg) {
	if !n.isFabric(m.From) {
		return
	}
	switch m.Type {
	case "CERT", "REPLY": // certificados TS/ACK do grupo Fabric (um por lote, várias tx)
		for i := range m.Certs {
			ct := m.Certs[i]
			if ct.Group != m.From || !verifyFabricCert(&ct, n.fsigners[ct.Group]) {
				x := ""
				if len(ct.Items) > 0 {
					x = ct.Items[0].TxID
				}
				n.trace("W_BAD_"+ct.Kind, x, TS{Clock: uint64(len(ct.Items)), Chain: fmt.Sprintf("sigs=%d", len(ct.Sigs))})
				continue
			}
			n.trace("W_OK_"+ct.Kind, "", TS{Clock: uint64(len(ct.Items))})
			if m.Type == "CERT" && m.Src > 0 {
				n.mu.Lock()
				n.fabCoord[ct.Group] = m.Src
				n.mu.Unlock()
			}
			for _, it := range ct.Items {
				d := destOf(it.TxID)
				if !contains(d, n.cfg.Chain) || !contains(d, ct.Group) {
					continue
				}
				v := TS{it.Clock, ct.Group}
				if ct.Kind == "ACK" {
					v = TS{it.Clock, it.Node}
				}
				cc := ct
				n.onCert(Cert{Kind: ct.Kind, Chain: ct.Group, TxID: it.TxID, Val: v, Fab: &cc})
			}
		}
	case "NEED": // o coordenador do Fabric pede o certificado TS/ACK deste grupo
		if m.Kind != "TS" && m.Kind != "ACK" {
			return
		}
		var certs []alg3Cert
		n.mu.Lock()
		for _, id := range m.TxIDs {
			if d := destOf(id); contains(d, m.From) && contains(d, n.cfg.Chain) {
				if c := n.myCerts[m.Kind+"|"+id]; c != nil {
					certs = append(certs, toWire(*c))
				}
			}
		}
		n.mu.Unlock()
		fg := n.cfg.Fabric[m.From]
		if len(certs) > 0 && m.Rep >= 1 && m.Rep <= len(fg.Addrs) {
			n.sendWire(fg.Addrs[m.Rep-1], alg3Msg{Type: "REPLY", From: n.cfg.Chain, Certs: certs, Src: n.me + 1})
		}
	case "START": // relay do START pelo Fabric: o envelope do cliente; o TxID vem do cabeçalho do canal
		id := envelopeTxID(m.Env)
		d := destOf(id)
		if id == "" || !contains(d, n.cfg.Chain) || !contains(d, m.From) || !n.validDest(d) {
			return
		}
		if _, conhece := n.app.lookup(id); conhece {
			return
		}
		n.mu.Lock()
		vai := time.Since(n.relayAt[id]) > 3*time.Second
		if vai {
			n.relayAt[id] = time.Now()
		}
		n.mu.Unlock()
		if vai {
			n.trace("RELAY", id, TS{})
			n.submit("S|" + id + "|" + strings.Join(d, ",") + "|" + base64.StdEncoding.EncodeToString(m.Env))
		}
	}
}

// envelopeTxID: tx_id (campo 5) do ChannelHeader de um common.Envelope do Fabric, lido direto do protobuf:
// Envelope.payload(1) → Payload.header(1) → Header.channel_header(1) → ChannelHeader.tx_id(5).
func envelopeTxID(env []byte) string {
	b := env
	for _, campo := range []protowire.Number{1, 1, 1} {
		b = protoField(b, campo)
		if b == nil {
			return ""
		}
	}
	return string(protoField(b, 5))
}

func protoField(b []byte, want protowire.Number) []byte {
	for len(b) > 0 {
		num, typ, n := protowire.ConsumeTag(b)
		if n < 0 {
			return nil
		}
		b = b[n:]
		m := protowire.ConsumeFieldValue(num, typ, b)
		if m < 0 {
			return nil
		}
		if num == want && typ == protowire.BytesType {
			v, _ := protowire.ConsumeBytes(b)
			return v
		}
		b = b[m:]
	}
	return nil
}
