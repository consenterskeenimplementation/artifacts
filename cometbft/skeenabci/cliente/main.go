// cliente: gera carga para o Skeen entre blockchains CometBFT. Tx locais vão à blockchain de casa (alternando entre as
// blockchains); tx globais (fração -cross) vão a TODAS as blockchains de destino (o R-multicast do START, Alg. 3, l. 5).
// Grava "<id> <envio em µs> <destinos>" por tx em -out, para a latência envio → entrega no último destino.
package main

import (
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"math/rand"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

func main() {
	rpcs := flag.String("rpc", "", "nome=url do RPC de cada blockchain, separados por vírgula (ex.: A=http://127.0.1.1:26657,B=...)")
	n := flag.Int("n", 1000, "total de tx")
	cross := flag.Float64("cross", 0.1, "fração de tx globais")
	rate := flag.Float64("rate", 200, "tx/s")
	pref := flag.String("prefixo", "t", "prefixo dos ids")
	parcial := flag.Float64("parcial", 0, "fração das tx globais enviadas só ao 1º destino (teste do relay de START entre coordenadores)")
	ndest := flag.Int("dest", 0, "destinos por tx global: a blockchain de casa + (dest-1) sorteadas; 0 = todas")
	out := flag.String("out", "envios.txt", "arquivo com o envio de cada tx")
	flag.Parse()
	type ch struct{ nome, url string }
	var chains []ch
	for _, kv := range strings.Split(*rpcs, ",") {
		p := strings.SplitN(kv, "=", 2)
		if len(p) == 2 {
			chains = append(chains, ch{p[0], p[1]})
		}
	}
	if len(chains) < 2 {
		fmt.Fprintln(os.Stderr, "precisa de pelo menos 2 blockchains em -rpc")
		os.Exit(2)
	}
	nomes := make([]string, len(chains))
	for i, c := range chains {
		nomes[i] = c.nome
	}
	f, err := os.Create(*out)
	if err != nil {
		panic(err)
	}
	defer f.Close()
	var mu sync.Mutex
	var wg sync.WaitGroup
	cli := &http.Client{Timeout: 5 * time.Second}
	// envia até a blockchain aceitar (mempool cheio ou falha de conexão: tenta de novo); "já está no cache" conta como
	// aceita. Um START aceito por um destino e recusado por outro travaria o primeiro (achado B4 do revisor); o relay
	// de START entre coordenadores (NEED TS) cobre o resto.
	var falhas sync.Map
	envia := func(url, tx string) {
		for tent := 0; tent < 200; tent++ {
			r, err := cli.Get(url + "/broadcast_tx_async?tx=0x" + hex.EncodeToString([]byte(tx)))
			if err == nil {
				var resp struct {
					Error *struct {
						Data string `json:"data"`
					} `json:"error"`
				}
				dec := json.NewDecoder(r.Body).Decode(&resp)
				r.Body.Close()
				if dec == nil && (resp.Error == nil || strings.Contains(resp.Error.Data, "already exists in cache")) {
					return
				}
			}
			time.Sleep(50 * time.Millisecond)
		}
		falhas.Store(tx, true)
		fmt.Fprintln(os.Stderr, "não aceita após 200 tentativas:", tx)
	}
	tick := time.NewTicker(time.Duration(float64(time.Second) / *rate))
	defer tick.Stop()
	rng := rand.New(rand.NewSource(1))
	for i := 0; i < *n; i++ {
		<-tick.C
		var tx, dest string
		var alvo []ch
		if rng.Float64() < *cross {
			id := fmt.Sprintf("%sg%d", *pref, i)
			alvo = chains
			if *ndest >= 2 && *ndest < len(chains) { // casa (rodízio) + sorteadas entre as demais, em ordem de nome
				casa := i % len(chains)
				outras := rng.Perm(len(chains))
				esc := map[int]bool{casa: true}
				for _, k := range outras {
					if len(esc) == *ndest {
						break
					}
					esc[k] = true
				}
				alvo = nil
				for k := range chains {
					if esc[k] {
						alvo = append(alvo, chains[k])
					}
				}
			}
			ns := make([]string, len(alvo))
			for k, c := range alvo {
				ns[k] = c.nome
			}
			dest = strings.Join(ns, ",")
			tx = "S|" + id + "|" + dest
			if *parcial > 0 && rng.Float64() < *parcial {
				alvo = alvo[:1] // os demais destinos só recebem o START pelo relay do coordenador (NEED TS)
			}
			mu.Lock()
			fmt.Fprintf(f, "%s %d %s\n", id, time.Now().UnixMicro(), dest)
			mu.Unlock()
		} else {
			c := chains[i%len(chains)]
			id := fmt.Sprintf("%sl%d", *pref, i)
			tx, alvo, dest = "L|"+id, []ch{c}, c.nome
			mu.Lock()
			fmt.Fprintf(f, "%s %d %s\n", id, time.Now().UnixMicro(), dest)
			mu.Unlock()
		}
		for _, c := range alvo {
			wg.Add(1)
			go func(u string) { defer wg.Done(); envia(u, tx) }(c.url)
		}
	}
	wg.Wait()
}
