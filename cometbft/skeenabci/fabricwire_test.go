package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	"testing"
	"time"

	"google.golang.org/protobuf/encoding/protowire"
)

// O mesmo vetor de TestAlg3DigestVetor (skeen_alg3/chain_hosts_test.go), calculado pelo código do Fabric.
func TestFabricDigestVetor(t *testing.T) {
	ct := &alg3Cert{Kind: "ACK", Group: "canal2", Seq: 42, Items: []alg3Item{
		{TxID: "CROSS_canal1-canal2_a", Clock: 17, Node: "canal1", Dest: []string{"canal1", "canal2"}},
		{TxID: "CROSS_canal1-canal2_b", Clock: 3, Dest: []string{"canal1", "canal2"}}}}
	const esperado = "ee7c07cb8d13bbb409585f6152938ffb386be0e77830fc90dc147a24ab18d5b1"
	if got := fmt.Sprintf("%x", ct.digest()); got != esperado {
		t.Fatalf("resumo %s, esperado %s (o do Fabric)", got, esperado)
	}
}

// orderer de teste: chave ECDSA P-256, certificado x509 autoassinado e a SerializedIdentity do MSP.
func ordererTeste(t *testing.T) (*ecdsa.PrivateKey, string, []byte) {
	k, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	tpl := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "orderer"},
		NotBefore: time.Now(), NotAfter: time.Now().Add(time.Hour)}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &k.PublicKey, k)
	if err != nil {
		t.Fatal(err)
	}
	p := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
	var id []byte
	id = protowire.AppendTag(id, 1, protowire.BytesType)
	id = protowire.AppendBytes(id, []byte("OrdererMSP"))
	id = protowire.AppendTag(id, 2, protowire.BytesType)
	id = protowire.AppendBytes(id, []byte(p))
	return k, p, id
}

// Certificado do grupo Fabric (canal1): f+1 assinaturas ECDSA válidas de orderers distintos e fixados.
func TestVerifyFabricCert(t *testing.T) {
	var pems []string
	var ks []*ecdsa.PrivateKey
	var ids [][]byte
	for i := 0; i < 4; i++ {
		k, p, id := ordererTeste(t)
		ks, pems, ids = append(ks, k), append(pems, p), append(ids, id)
	}
	signers, err := parseFabricSigners(pems)
	if err != nil {
		t.Fatal(err)
	}
	ct := &alg3Cert{Kind: "TS", Group: "canal1", Seq: 9, Items: []alg3Item{{TxID: "CROSS_canal1-canal2_z", Clock: 4, Dest: []string{"canal1", "canal2"}}}}
	d := ct.digest()
	h := sha256.Sum256(d[:])
	sig := func(i int) alg3Sig {
		s, _ := ecdsa.SignASN1(rand.Reader, ks[i], h[:])
		return alg3Sig{ID: ids[i], Sig: s}
	}
	ct.Sigs = []alg3Sig{sig(0)}
	if verifyFabricCert(ct, signers) {
		t.Fatal("aceitou com f assinaturas")
	}
	ct.Sigs = []alg3Sig{sig(0), sig(3)}
	if !verifyFabricCert(ct, signers) {
		t.Fatal("recusou f+1 válidas")
	}
	_, _, estranho := ordererTeste(t)
	ct.Sigs = []alg3Sig{sig(0), {ID: estranho, Sig: ct.Sigs[1].Sig}}
	if verifyFabricCert(ct, signers) {
		t.Fatal("aceitou orderer não fixado")
	}
}
