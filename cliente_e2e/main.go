package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/asn1"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"flag"
	"fmt"
	"hash/fnv"
	"math/big"
	mrand "math/rand/v2"
	"net"
	"net/http"
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/hyperledger/fabric-protos-go-apiv2/common"
	"github.com/hyperledger/fabric-protos-go-apiv2/msp"
	"github.com/hyperledger/fabric-protos-go-apiv2/orderer"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

// =============================================================================
// 1. FLAGS
// =============================================================================

var (
	flagTotalTx   = flag.Int("tx", 111, "Total de transações (ignorado se -duration > 0)")
	flagShards    = flag.Int("shards", 4, "Shards por transação cross-shard (1–4)")
	flagCrossProb = flag.Float64("cross", 1.0, "Probabilidade de cross-shard (0.0–1.0)")
	flagPayload   = flag.Int("payload", 128, "Tamanho do payload em bytes")
	// Skeen entre plataformas (16/11/2026, Fabric × CometBFT; cometbft/PLANO_HETERO.md): canais servidos por uma
	// blockchain CometBFT, "canal2=http://127.0.2.2:26657,...". A tx com destino nesses canais vai pela RPC do CometBFT,
	// com o MESMO TxID ("S|<TxID>|<destinos>" para a global, "L|<TxID>" para a local), em vez do Broadcast do Fabric.
	flagCMT         = flag.String("cmt", "", "canais servidos pelo CometBFT: canal=url_rpc, separados por vírgula")
	// relay CometBFT → Fabric (17/11/2026): o START que vai ao CometBFT leva o envelope assinado para o Fabric, e esta
	// fração das globais entre as plataformas vai SÓ ao CometBFT (o coordenador dele reenvia o START ao Fabric)
	flagSoCMT       = flag.Float64("so-cmt", 0, "fração das globais Fabric×CometBFT enviadas só ao CometBFT (teste do relay de START)")
	flagWorkers     = flag.Int("workers", 100, "Máximo de transações simultâneas")
	flagPinShards   = flag.Bool("pin-shards", false, "cross=0: fixa workers/N workers em cada shard (laço fechado por shard, sem sorteio de shard por tx) e imprime o TPS por shard; exige -duration")
	flagHome        = flag.String("home", "", "Canal de casa deste processo-cliente (um cliente por local, teste de WAN): a tx local vai a ele e a cross vai a ele + (-shards)-1 canais sorteados entre os demais")
	flagParceiros   = flag.String("parceiros", "", "Com -home: canais (separados por vírgula) entre os quais a tx cross sorteia os outros destinos, em vez de todos os demais (teste de WAN com as tx globais num só par de regiões)")
	flagRate        = flag.Float64("rate", 0, "Teto de tx/s deste processo-cliente (0 = sem teto: só o laço fechado de -workers); cada nova tx espera a próxima batida de um relógio nesse ritmo, para igualar a carga entre clientes (teste de WAN)")
	flagDuration    = flag.Int("duration", 0, "Duração em segundos (0 = usar -tx)")
	flagWarmup      = flag.Int("warmup", 0, "Segundos de aquecimento no início do run, descartados das métricas (TPS/latência não contam essas transações)")
	flagCooldown    = flag.Int("cooldown", 0, "Segundos finais do run (antes do -duration acabar) descartados das métricas, mesmo tratamento do -warmup mas no fim; exige -duration > 0")
	flagConfig      = flag.String("config", "", "Arquivo de endereços (canal=addr por linha)")
	flagSignWorkers = flag.Int("sign-workers", 0, "Workers de assinatura ECDSA (0 = NumCPU)")
	flagRPCTimeout  = flag.Int("rpc-timeout", 10, "Timeout por chamada gRPC em segundos")

	flagOutRaw = flag.String("out-raw", "", "Exportar latências brutas para CSV (ex: raw_100w.csv)")
	flagOutSum = flag.String("out-sum", "summary.csv", "Acrescentar resumo da execução neste CSV")

	flagSeed = flag.Int64("seed", 0, "Semente do RNG (0 = automática; passe valores distintos por processo-cliente)")

	flagCFTReplicas = flag.Int("cft-replicas", 1, "Nº de réplicas candidatas por shard (Skeen-CFT com coordenador dinâmico: tenta base, base+1, ... base+N-1; 1 = comportamento antigo, só a réplica base)")
	flagBFTReplicas = flag.Int("bft-replicas", 1, "Igual a -cft-replicas, mas para Skeen-BFT com coordenador dinâmico (SKEEN_BFT_REPLICA_ID)")

	// flagProbeLeader: o Broadcast() do Fabric retorna Status_SUCCESS tanto do
	// líder real quanto de um seguidor que faz forward interno pro líder — a
	// lógica de retry em candidateAddrs (abaixo) nunca detecta essa diferença
	// porque a PRIMEIRA réplica tentada já responde SUCCESS (só mais devagar,
	// pelo hop extra). Sem essa sonda, o cliente fica preso na réplica base
	// (ex.: sempre a réplica 1), e cada bateria mede uma mistura aleatória de
	// "acertei o líder" (rápido) e "caí num seguidor" (lento, +hop) —
	// dependendo de qual réplica venceu a eleição do Raft/BFT daquela vez.
	// Com essa flag, antes da medição real o cliente manda algumas
	// transações de sonda pra cada réplica candidata, mede a latência média
	// de cada uma, e fixa a mais rápida (que é, na prática, o líder real)
	// como endereço inicial — eliminando esse viés de amostragem.
	flagProbeLeader = flag.Bool("probe-leader", false, "Sonda as réplicas candidatas antes da medição e fixa a mais rápida (evita viés de forward pro líder real em CFT/BFT)")
	flagProbeCount  = flag.Int("probe-count", 5, "Nº de transações de sonda por réplica candidata (só com -probe-leader)")

	// flagMeasureCommit: Broadcast() do Fabric retorna Status_SUCCESS assim que
	// a transação é ACEITA pra ordenação, não quando é de fato confirmada pelo
	// quórum e gravada no bloco — então a latência normal (IntraAvgMs) não
	// captura o RTT de replicação do CFT/BFT/Raft. Com essa flag, a latência
	// registrada passa a ser do envio até o bloco contendo a tx chegar via
	// Deliver (commit real) — só suportado com -shards 1 (1 canal fixo).
	flagMeasureCommit = flag.Bool("measure-commit", false, "Mede latência até o COMMIT real (via Deliver), não só o ack do Broadcast — requer -shards 1")
	flagCommitTimeout = flag.Int("commit-timeout", 30, "Timeout em segundos esperando o commit via Deliver (só com -measure-commit)")
)

// =============================================================================
// 2. CONFIGURAÇÃO DE SHARDS
// =============================================================================

type shardRouter map[string]string

func loadRouter() shardRouter {
	defaults := shardRouter{
		"canal1": "127.0.0.1:7050",
		"canal2": "127.0.0.1:8050",
		"canal3": "127.0.0.1:9050",
		"canal4": "127.0.0.1:10050",
	}

	if *flagConfig == "" {
		return defaults
	}

	data, err := os.ReadFile(*flagConfig)
	if err != nil {
		fmt.Fprintf(os.Stderr, "⚠️  config não encontrado: %v — usando defaults\n", err)
		return defaults
	}

	r := make(shardRouter)
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) == 2 {
			r[strings.TrimSpace(parts[0])] = strings.TrimSpace(parts[1])
		}
	}
	return r
}

// =============================================================================
// 3. POOL DE CONEXÕES gRPC
// =============================================================================

type grpcPool struct {
	mu      sync.RWMutex
	conns   map[string]*grpc.ClientConn
	clients map[string]orderer.AtomicBroadcastClient
	creds   credentials.TransportCredentials
}

func newGRPCPool(creds credentials.TransportCredentials) *grpcPool {
	return &grpcPool{
		conns:   make(map[string]*grpc.ClientConn),
		clients: make(map[string]orderer.AtomicBroadcastClient),
		creds:   creds,
	}
}

func (p *grpcPool) get(addr string) (orderer.AtomicBroadcastClient, error) {
	p.mu.RLock()
	if c, ok := p.clients[addr]; ok {
		p.mu.RUnlock()
		return c, nil
	}
	p.mu.RUnlock()

	p.mu.Lock()
	defer p.mu.Unlock()

	if c, ok := p.clients[addr]; ok {
		return c, nil
	}

	opts := []grpc.DialOption{grpc.WithTransportCredentials(p.creds)}
	if clientBind != nil {
		d := net.Dialer{LocalAddr: clientBind}
		opts = append(opts, grpc.WithContextDialer(func(ctx context.Context, a string) (net.Conn, error) {
			return d.DialContext(ctx, "tcp", a)
		}))
	}
	conn, err := grpc.Dial(addr, opts...)
	if err != nil {
		return nil, err
	}
	client := orderer.NewAtomicBroadcastClient(conn)
	p.conns[addr] = conn
	p.clients[addr] = client
	return client, nil
}

// candidateAddrs gera os endereços candidatos de um canal. Duas formas:
//  1. Lista explícita: o valor no config já vem como "addr1,addr2,addr3"
//     (usado quando as réplicas não seguem a convenção de porta+1, por
//     exemplo Raft/BFT-SMaRt nativos, cujas réplicas usam portas espaçadas
//     de 1000 — 7050/8050/9050 — não consecutivas).
//  2. Derivada: incrementa a porta do endereço base (base, base+1, ...,
//     base+n-1) — convenção de portas por réplica usada nos scripts de
//     bateria do Skeen-CFT/BFT (grpc = shardBase + (réplica-1)).
//
// Em ambos os casos, usado pelo cliente pra tentar outras réplicas do mesmo
// canal quando a atual não é a coordenadora/líder atual (ver isCoordinator/
// groupCoord no consenter Skeen, e probeLeader pra Raft/BFT-SMaRt puros).
func candidateAddrs(base string, n int) []string {
	if strings.Contains(base, ",") {
		parts := strings.Split(base, ",")
		out := make([]string, 0, len(parts))
		for _, p := range parts {
			p = strings.TrimSpace(p)
			if p != "" {
				out = append(out, p)
			}
		}
		if len(out) > 0 {
			return out
		}
	}
	if n <= 1 {
		return []string{base}
	}
	idx := strings.LastIndex(base, ":")
	if idx < 0 {
		return []string{base}
	}
	host := base[:idx]
	port, err := strconv.Atoi(base[idx+1:])
	if err != nil {
		return []string{base}
	}
	out := make([]string, n)
	for i := 0; i < n; i++ {
		out[i] = fmt.Sprintf("%s:%d", host, port+i)
	}
	return out
}

// lastGoodAddr guarda, por canal, o endereço de réplica que respondeu com
// sucesso pela última vez — tentado primeiro na próxima submissão.
var lastGoodAddr sync.Map // canal (string) -> addr (string)

// probeLeader manda algumas transações de sonda pra cada réplica candidata
// de cada canal, mede a latência média de round-trip do Broadcast, e fixa
// (via lastGoodAddr) a réplica mais rápida — na prática, o líder real do
// grupo Raft/BFT daquele canal, evitando o hop extra de forward que uma
// réplica seguidora faz internamente antes de responder SUCCESS. Ver
// comentário de flagProbeLeader pra motivação completa.
func probeLeader(router shardRouter, allShards []string, pool *grpcPool, builder *envBuilder, replicaCandidates int, probeCount int) {
	if replicaCandidates <= 1 {
		return
	}
	fmt.Println("🔍 Sondando réplicas pra achar o líder real (evita viés de forward)...")
	for _, c := range allShards {
		candidates := candidateAddrs(router[c], replicaCandidates)
		bestAddr := ""
		bestAvg := time.Duration(1<<62 - 1)
		for i, addr := range candidates {
			var total time.Duration
			okCount := 0
			for p := 0; p < probeCount; p++ {
				label := fmt.Sprintf("PROBE_%s_%d_%d_%d", c, i, p, time.Now().UnixNano())
				env, err := builder.build(c, label, 128)
				if err != nil {
					continue
				}
				client, err := pool.get(addr)
				if err != nil {
					continue
				}
				t0 := time.Now()
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				stream, err := client.Broadcast(ctx)
				if err != nil {
					cancel()
					continue
				}
				if err := stream.Send(env); err != nil {
					cancel()
					continue
				}
				reply, err := stream.Recv()
				cancel()
				if err != nil || reply.Status != common.Status_SUCCESS {
					continue
				}
				total += time.Since(t0)
				okCount++
			}
			if okCount == 0 {
				fmt.Printf("   %s: %s sem resposta na sonda\n", c, addr)
				continue
			}
			avg := total / time.Duration(okCount)
			fmt.Printf("   %s: %s -> %v média (%d/%d ok)\n", c, addr, avg, okCount, probeCount)
			if avg < bestAvg {
				bestAvg = avg
				bestAddr = addr
			}
		}
		if bestAddr != "" {
			lastGoodAddr.Store(c, bestAddr)
			fmt.Printf("   %s: fixando %s (líder provável, %v)\n", c, bestAddr, bestAvg)
		}
	}
}

func (p *grpcPool) closeAll() {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, conn := range p.conns {
		conn.Close()
	}
}

// =============================================================================
// 4. WORKER POOL DE ASSINATURA ECDSA
// =============================================================================

type signRequest struct {
	payload []byte
	result  chan signResult
}

type signResult struct {
	sig []byte
	err error
}

type signerPool struct {
	queue chan signRequest
}

func newSignerPool(ecdsaKey *ecdsa.PrivateKey, workers int) *signerPool {
	if workers <= 0 {
		workers = runtime.NumCPU()
	}
	p := &signerPool{
		queue: make(chan signRequest, workers*4),
	}
	for i := 0; i < workers; i++ {
		go p.worker(ecdsaKey)
	}
	return p
}

func (p *signerPool) worker(key *ecdsa.PrivateKey) {
	for req := range p.queue {
		hash := sha256.Sum256(req.payload)
		r, s, err := ecdsa.Sign(rand.Reader, key, hash[:])
		if err != nil {
			req.result <- signResult{err: err}
			continue
		}
		halfOrder := new(big.Int).Div(key.Curve.Params().N, big.NewInt(2))
		if s.Cmp(halfOrder) == 1 {
			s.Sub(key.Curve.Params().N, s)
		}
		sig, err := asn1.Marshal(struct{ R, S *big.Int }{r, s})
		req.result <- signResult{sig: sig, err: err}
	}
}

func (p *signerPool) sign(payload []byte) ([]byte, error) {
	ch := make(chan signResult, 1)
	p.queue <- signRequest{payload: payload, result: ch}
	res := <-ch
	return res.sig, res.err
}

// =============================================================================
// 5. CONSTRUÇÃO DE ENVELOPE
// =============================================================================

type envBuilder struct {
	sIdBytes   []byte
	signerPool *signerPool
}

func newEnvBuilder(key *ecdsa.PrivateKey, signWorkers int) (*envBuilder, error) {
	certPath := "./crypto-config/ordererOrganizations/example.com/users/Admin@example.com/msp/signcerts/Admin@example.com-cert.pem"
	certBytes, err := os.ReadFile(certPath)
	if err != nil {
		return nil, fmt.Errorf("cert: %w", err)
	}
	sIdBytes, err := proto.Marshal(&msp.SerializedIdentity{
		Mspid: "OrdererMSP", IdBytes: certBytes,
	})
	if err != nil {
		return nil, fmt.Errorf("serialized identity: %w", err)
	}

	return &envBuilder{
		sIdBytes:   sIdBytes,
		signerPool: newSignerPool(key, signWorkers),
	}, nil
}

func (b *envBuilder) build(channelID, txID string, payloadSize int) (*common.Envelope, error) {
	nonce := make([]byte, 24)
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}

	chdr, _ := proto.Marshal(&common.ChannelHeader{
		ChannelId: channelID, Type: 3, TxId: txID,
	})
	shdr, _ := proto.Marshal(&common.SignatureHeader{
		Creator: b.sIdBytes, Nonce: nonce,
	})
	payloadBytes, _ := proto.Marshal(&common.Payload{
		Header: &common.Header{
			ChannelHeader: chdr, SignatureHeader: shdr,
		},
		Data: make([]byte, payloadSize),
	})

	sig, err := b.signerPool.sign(payloadBytes)
	if err != nil {
		return nil, err
	}

	return &common.Envelope{Payload: payloadBytes, Signature: sig}, nil
}

// buildSeekEnvelope monta o envelope assinado exigido pela API Deliver do
// Fabric: primeiro item enviado no stream, tipo DELIVER_SEEK_INFO, payload =
// SeekInfo marshalled. SeekNewest (ignora o backlog, só blocos novos a partir
// de agora) até SeekSpecified{MaxUint64} com BLOCK_UNTIL_READY = "continue
// entregando blocos novos indefinidamente".
func (b *envBuilder) buildSeekEnvelope(channelID string) (*common.Envelope, error) {
	nonce := make([]byte, 24)
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	seek, err := proto.Marshal(&orderer.SeekInfo{
		Start:    &orderer.SeekPosition{Type: &orderer.SeekPosition_Newest{Newest: &orderer.SeekNewest{}}},
		Stop:     &orderer.SeekPosition{Type: &orderer.SeekPosition_Specified{Specified: &orderer.SeekSpecified{Number: ^uint64(0)}}},
		Behavior: orderer.SeekInfo_BLOCK_UNTIL_READY,
	})
	if err != nil {
		return nil, err
	}
	chdr, _ := proto.Marshal(&common.ChannelHeader{
		ChannelId: channelID, Type: int32(common.HeaderType_DELIVER_SEEK_INFO),
		Timestamp: timestamppb.Now(),
	})
	shdr, _ := proto.Marshal(&common.SignatureHeader{Creator: b.sIdBytes, Nonce: nonce})
	payloadBytes, _ := proto.Marshal(&common.Payload{
		Header: &common.Header{ChannelHeader: chdr, SignatureHeader: shdr},
		Data:   seek,
	})
	sig, err := b.signerPool.sign(payloadBytes)
	if err != nil {
		return nil, err
	}
	return &common.Envelope{Payload: payloadBytes, Signature: sig}, nil
}

// =============================================================================
// 5b. MEDIÇÃO DE LATÊNCIA DE COMMIT REAL (via Deliver, -measure-commit)
// =============================================================================

// pendingCommits: txID -> *pendingCommit, preenchido quando o Broadcast() é
// aceito; o watcher do Deliver resolve (fecha `done`) quando vê a tx num
// bloco de fato commitado. Broadcast() só confirma "aceito pra ordenação",
// não "commitado" — para CFT/BFT/Raft isso esconde o RTT de replicação.
type pendingCommit struct {
	txStart time.Time
	done    chan time.Duration
}

var pendingCommits sync.Map

// clientBind: endereço de origem das conexões gRPC (com -home, o IP do canal de casa); nil = escolha do kernel.
var clientBind net.Addr

// watchDeliverForCommits conecta ao endereço via Deliver, faz seek a partir
// do bloco mais novo, e resolve pendingCommits conforme os TxIds aparecem em
// blocos recebidos. Roda pra sempre numa goroutine dedicada (1 por canal
// monitorado); reconecta em caso de erro de stream.
func watchDeliverForCommits(addr, channelID string, pool *grpcPool, builder *envBuilder) {
	for {
		client, err := pool.get(addr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "[measure-commit] pool.get %s: %v\n", addr, err)
			time.Sleep(time.Second)
			continue
		}
		seekEnv, err := builder.buildSeekEnvelope(channelID)
		if err != nil {
			fmt.Fprintf(os.Stderr, "[measure-commit] seek envelope: %v\n", err)
			time.Sleep(time.Second)
			continue
		}
		ctx, cancel := context.WithCancel(context.Background())
		stream, err := client.Deliver(ctx)
		if err != nil {
			cancel()
			fmt.Fprintf(os.Stderr, "[measure-commit] Deliver %s: %v\n", addr, err)
			time.Sleep(time.Second)
			continue
		}
		if err := stream.Send(seekEnv); err != nil {
			cancel()
			fmt.Fprintf(os.Stderr, "[measure-commit] seek send %s: %v\n", addr, err)
			time.Sleep(time.Second)
			continue
		}

		for {
			resp, err := stream.Recv()
			if err != nil {
				fmt.Fprintf(os.Stderr, "[measure-commit] deliver recv %s: %v (reconectando)\n", addr, err)
				break
			}
			blk := resp.GetBlock()
			if blk == nil || blk.Data == nil {
				continue
			}
			now := time.Now()
			for _, txBytes := range blk.Data.Data {
				env := &common.Envelope{}
				if proto.Unmarshal(txBytes, env) != nil {
					continue
				}
				payload := &common.Payload{}
				if proto.Unmarshal(env.Payload, payload) != nil || payload.Header == nil {
					continue
				}
				chdr := &common.ChannelHeader{}
				if proto.Unmarshal(payload.Header.ChannelHeader, chdr) != nil || chdr.TxId == "" {
					continue
				}
				if v, ok := pendingCommits.LoadAndDelete(chdr.TxId); ok {
					pc := v.(*pendingCommit)
					pc.done <- now.Sub(pc.txStart)
				}
			}
		}
		cancel()
	}
}

// =============================================================================
// 6. RECORDER DE LATÊNCIA E EXPORTAÇÃO CSV
// =============================================================================

// recorderStripes: o registro de latência era protegido por 1 mutex global,
// que virava gargalo quando um único processo-cliente atende vários shards
// simultaneamente (todas as goroutines de todos os shards serializam no mesmo
// lock a cada transação). Com múltiplos processos (1 por shard) isso nunca
// aparecia, pois cada processo tinha seu próprio mutex. Striped locking
// distribui a contenção por N mutexes independentes, escolhidos por id da tx.
const recorderStripes = 64

type recorderStripe struct {
	mu           sync.Mutex
	localSamples []time.Duration
	crossSamples []time.Duration
}

type latencyRecorder struct {
	stripes [recorderStripes]recorderStripe
	done    atomic.Int64
	errors  atomic.Int64
}

func (r *latencyRecorder) record(id int, d time.Duration, isCross bool) {
	s := &r.stripes[id%recorderStripes]
	s.mu.Lock()
	if isCross {
		s.crossSamples = append(s.crossSamples, d)
	} else {
		s.localSamples = append(s.localSamples, d)
	}
	s.mu.Unlock()
	r.done.Add(1)
}

func (r *latencyRecorder) printReport(totalDuration float64) {
	var localSamples, crossSamples []time.Duration
	for i := range r.stripes {
		r.stripes[i].mu.Lock()
		localSamples = append(localSamples, r.stripes[i].localSamples...)
		crossSamples = append(crossSamples, r.stripes[i].crossSamples...)
		r.stripes[i].mu.Unlock()
	}

	total := len(localSamples) + len(crossSamples)
	tps := float64(total) / totalDuration

	fmt.Printf("\n🏁 BENCHMARK CONCLUÍDO\n")
	fmt.Printf("   Tempo total  : %.2f s\n", totalDuration)
	fmt.Printf("   Txs OK       : %d\n", total)
	fmt.Printf("   Txs erro     : %d\n", r.errors.Load())
	fmt.Printf("   TPS global   : %.2f\n", tps)

	getStats := func(samples []time.Duration) (avg, p50, p99 float64) {
		if len(samples) == 0 {
			return 0, 0, 0
		}
		sort.Slice(samples, func(i, j int) bool { return samples[i] < samples[j] })
		n := len(samples)
		var sum time.Duration
		for _, d := range samples {
			sum += d
		}
		avg = float64(sum.Milliseconds()) / float64(n)
		p50 = float64(samples[n*50/100].Milliseconds())
		p99 = float64(samples[n*99/100].Milliseconds())
		return
	}

	avgLoc, p50Loc, p99Loc := getStats(localSamples)
	avgCro, p50Cro, p99Cro := getStats(crossSamples)

	fmt.Printf("\n📊 LATÊNCIA CROSS-SHARD (ms)\n")
	fmt.Printf("   Avg: %.2f | P50: %.2f | P99: %.2f\n", avgCro, p50Cro, p99Cro)
	fmt.Printf("\n📊 LATÊNCIA INTRA-SHARD (ms)\n")
	fmt.Printf("   Avg: %.2f | P50: %.2f | P99: %.2f\n", avgLoc, p50Loc, p99Loc)

	if *flagOutSum != "" {
		f, err := os.OpenFile(*flagOutSum, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err == nil {
			stat, _ := f.Stat()
			if stat.Size() == 0 {
				f.WriteString("Workers,Shards,CrossProb,Duration,TPS,Errors,CrossAvgMs,CrossP50Ms,CrossP99Ms\n")
			}
			f.WriteString(fmt.Sprintf("%d,%d,%.2f,%.2f,%.2f,%d,%.2f,%.2f,%.2f\n",
				*flagWorkers, *flagShards, *flagCrossProb, totalDuration, tps, r.errors.Load(), avgCro, p50Cro, p99Cro))
			f.Close()
		}
	}

	if *flagOutRaw != "" {
		f, err := os.Create(*flagOutRaw)
		if err == nil {
			f.WriteString("Type,LatencyMs\n")
			for _, d := range localSamples {
				f.WriteString(fmt.Sprintf("Local,%.2f\n", float64(d.Microseconds())/1000.0))
			}
			for _, d := range crossSamples {
				f.WriteString(fmt.Sprintf("Cross,%.2f\n", float64(d.Microseconds())/1000.0))
			}
			f.Close()
		}
	}
}

// =============================================================================
// 7. MAIN
// =============================================================================

// cmtRPC: canal → URL da RPC do CometBFT (-cmt).
var cmtRPC = map[string]string{}

var cmtHTTP = &http.Client{Timeout: 5 * time.Second}

// cmtBroadcast envia a tx ao mempool do CometBFT e tenta de novo até ser aceita ("já está no cache" conta como aceita).
func cmtBroadcast(url, tx string) error {
	var last error
	for tent := 0; tent < 200; tent++ {
		r, err := cmtHTTP.Get(url + "/broadcast_tx_async?tx=0x" + hex.EncodeToString([]byte(tx)))
		if err == nil {
			var resp struct {
				Error *struct {
					Data string `json:"data"`
				} `json:"error"`
			}
			err = json.NewDecoder(r.Body).Decode(&resp)
			r.Body.Close()
			if err == nil && (resp.Error == nil || strings.Contains(resp.Error.Data, "already exists in cache")) {
				return nil
			}
			if err == nil {
				err = fmt.Errorf("%s", resp.Error.Data)
			}
		}
		last = err
		time.Sleep(50 * time.Millisecond)
	}
	return last
}

func main() {
	flag.Parse()
	for _, kv := range strings.Split(*flagCMT, ",") {
		if p := strings.SplitN(kv, "=", 2); len(p) == 2 {
			cmtRPC[p[0]] = p[1]
		}
	}

	router := loadRouter()
	allShards := make([]string, 0, len(router))
	for k := range router {
		allShards = append(allShards, k)
	}
	sort.Strings(allShards)

	// garante que -shards não excede shards disponíveis no config
	if *flagShards > len(allShards) {
		*flagShards = len(allShards)
	}

	var homeOthers []string
	if *flagHome != "" {
		if _, ok := router[*flagHome]; !ok {
			fmt.Fprintf(os.Stderr, "-home %s não está no -config\n", *flagHome)
			os.Exit(1)
		}
		if *flagPinShards {
			fmt.Fprintln(os.Stderr, "-home e -pin-shards são incompatíveis")
			os.Exit(1)
		}
		for _, c := range allShards {
			if c != *flagHome {
				homeOthers = append(homeOthers, c)
			}
		}
		if *flagParceiros != "" {
			homeOthers = nil
			for _, c := range strings.Split(*flagParceiros, ",") {
				if _, ok := router[c]; !ok || c == *flagHome {
					fmt.Fprintf(os.Stderr, "-parceiros: %q não é outro canal do -config\n", c)
					os.Exit(1)
				}
				homeOthers = append(homeOthers, c)
			}
		}
		if *flagCrossProb > 0 && len(homeOthers) < *flagShards-1 {
			fmt.Fprintf(os.Stderr, "-home/-parceiros: só %d outros canais para tx cross com %d destinos\n", len(homeOthers), *flagShards)
			os.Exit(1)
		}
		// sai pelo IP do canal de casa: o filtro de latência por origem (teste de WAN) identifica o local por ele
		host, _, err := net.SplitHostPort(candidateAddrs(router[*flagHome], 1)[0])
		if ip := net.ParseIP(host); err == nil && ip != nil && !ip.Equal(net.IPv4(127, 0, 0, 1)) {
			clientBind = &net.TCPAddr{IP: ip}
		}
	}

	tlsCertPath := "./crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.crt"
	tlsKeyPath := "./crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/tls/server.key"
	caPath := "./crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/tls/ca.crt"

	tlsCert, err := tls.LoadX509KeyPair(tlsCertPath, tlsKeyPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "TLS cert: %v\n", err)
		os.Exit(1)
	}
	caCert, err := os.ReadFile(caPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "CA cert: %v\n", err)
		os.Exit(1)
	}
	caCertPool := x509.NewCertPool()
	caCertPool.AppendCertsFromPEM(caCert)
	creds := credentials.NewTLS(&tls.Config{
		Certificates:       []tls.Certificate{tlsCert},
		RootCAs:            caCertPool,
		InsecureSkipVerify: true,
	})

	keyDir := "./crypto-config/ordererOrganizations/example.com/users/Admin@example.com/msp/keystore/"
	files, err := os.ReadDir(keyDir)
	if err != nil || len(files) == 0 {
		fmt.Fprintf(os.Stderr, "keystore: %v\n", err)
		os.Exit(1)
	}
	keyBytes, _ := os.ReadFile(keyDir + files[0].Name())
	block, _ := pem.Decode(keyBytes)
	privKey, _ := x509.ParsePKCS8PrivateKey(block.Bytes)
	ecdsaKey := privKey.(*ecdsa.PrivateKey)

	pool := newGRPCPool(creds)
	defer pool.closeAll()

	replicaCandidatesMax := *flagCFTReplicas
	if *flagBFTReplicas > replicaCandidatesMax {
		replicaCandidatesMax = *flagBFTReplicas
	}

	fmt.Println("⏳ Aquecendo conexões gRPC...")
	for canal, addr := range router {
		for _, cand := range candidateAddrs(addr, replicaCandidatesMax) {
			if _, err := pool.get(cand); err != nil {
				fmt.Fprintf(os.Stderr, "Falha ao conectar em %s (%s): %v\n", canal, cand, err)
				os.Exit(1)
			}
		}
	}
	fmt.Println("✅ Conexões prontas")

	builder, err := newEnvBuilder(ecdsaKey, *flagSignWorkers)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Builder: %v\n", err)
		os.Exit(1)
	}

	if *flagProbeLeader {
		probeLeader(router, allShards, pool, builder, replicaCandidatesMax, *flagProbeCount)
	}

	if *flagMeasureCommit {
		if *flagShards != 1 {
			fmt.Fprintln(os.Stderr, "-measure-commit só suporta -shards 1")
			os.Exit(1)
		}
		commitChannel := allShards[0]
		commitAddr := candidateAddrs(router[commitChannel], replicaCandidatesMax)[0]
		go watchDeliverForCommits(commitAddr, commitChannel, pool, builder)
		fmt.Printf("📡 Medindo latência de COMMIT real via Deliver em %s (%s)\n", commitChannel, commitAddr)
	}

	// Semente explícita: reprodutibilidade e distribuição distinta entre processos
	clientSeed := uint64(*flagSeed)
	if clientSeed == 0 {
		clientSeed = uint64(time.Now().UnixNano())
	}

	runID := time.Now().UnixMilli()
	recorder := &latencyRecorder{}
	sem := make(chan struct{}, *flagWorkers)
	// limita goroutines internas (shard): workers × shards para evitar explosão de goroutines
	semShard := make(chan struct{}, *flagWorkers**flagShards)
	var wg sync.WaitGroup

	modoDesc := fmt.Sprintf("%d txs", *flagTotalTx)
	if *flagDuration > 0 {
		modoDesc = fmt.Sprintf("%ds duração fixa", *flagDuration)
	}
	signWk := *flagSignWorkers
	if signWk <= 0 {
		signWk = runtime.NumCPU()
	}
	fmt.Printf("\n🚀 SKEEN BENCHMARK\n")
	fmt.Printf("   Modo         : %s\n", modoDesc)
	fmt.Printf("   Shards/tx    : %d | Cross: %.0f%%\n", *flagShards, *flagCrossProb*100)
	fmt.Printf("   Workers tx   : %d | Sign workers: %d\n", *flagWorkers, signWk)
	fmt.Printf("   Payload      : %d bytes\n", *flagPayload)
	fmt.Printf("   GOMAXPROCS   : %d (núcleos disponíveis via CPU affinity/taskset)\n", runtime.GOMAXPROCS(0))
	fmt.Printf("   RNG seed     : %d\n\n", clientSeed)

	start := time.Now()
	deadline := time.Time{}
	if *flagDuration > 0 {
		deadline = start.Add(time.Duration(*flagDuration) * time.Second)
	}
	measureStart := start
	if *flagWarmup > 0 {
		measureStart = start.Add(time.Duration(*flagWarmup) * time.Second)
		fmt.Printf("   Warmup       : %ds (descartado das métricas)\n", *flagWarmup)
	}
	measureEnd := time.Time{}
	if *flagCooldown > 0 && *flagDuration > 0 {
		measureEnd = deadline.Add(-time.Duration(*flagCooldown) * time.Second)
		fmt.Printf("   Cooldown     : %ds (descartado das métricas, no fim)\n", *flagCooldown)
	}

	shardOK := make(map[string]*atomic.Int64, len(allShards))
	for _, c := range allShards {
		shardOK[c] = &atomic.Int64{}
	}
	var ritmo <-chan time.Time // -rate: uma batida por tx; batidas perdidas (workers ocupados) não acumulam
	if *flagRate > 0 {
		tk := time.NewTicker(time.Duration(float64(time.Second) / *flagRate))
		defer tk.Stop()
		ritmo = tk.C
		fmt.Printf("   Teto de vazão: %.0f tx/s\n", *flagRate)
	}
	loop := func(pinned string, sem chan struct{}, idBase int) {
		txSeq := 0
		for {
			if *flagDuration > 0 {
				if time.Now().After(deadline) {
					break
				}
			} else {
				if txSeq >= *flagTotalTx {
					break
				}
			}
			txSeq++
			id := idBase + txSeq

			if ritmo != nil {
				<-ritmo
			}
			wg.Add(1)
			sem <- struct{}{}

			go func(id int) {
				defer wg.Done()
				defer func() { <-sem }()

				shards := make([]string, len(allShards))
				copy(shards, allShards)

				// RNG por transação: determinístico (seed+id) e sem lock — goroutine-safe
				txRng := mrand.New(mrand.NewPCG(clientSeed+uint64(id), clientSeed>>3))
				isCross := txRng.Float64() < *flagCrossProb && *flagShards > 1
				if pinned != "" {
					isCross = false
				}
				var targets []string
				if isCross && *flagHome != "" {
					// casa em primeiro: deixa o txID único entre processos-cliente (o orderer ordena os destinos)
					outros := append([]string(nil), homeOthers...)
					txRng.Shuffle(len(outros), func(i, j int) { outros[i], outros[j] = outros[j], outros[i] })
					targets = append([]string{*flagHome}, outros[:*flagShards-1]...)
				} else if isCross {
					txRng.Shuffle(len(shards), func(i, j int) {
						shards[i], shards[j] = shards[j], shards[i]
					})
					targets = shards[:*flagShards]
				} else {
					if pinned != "" {
						targets = []string{pinned}
					} else if *flagHome != "" {
						targets = []string{*flagHome}
					} else {
						targets = []string{shards[txRng.IntN(len(shards))]}
					}
				}

				txLabel := fmt.Sprintf("CROSS_%s_RUN%d_%d", strings.Join(targets, "-"), runID, id)
				// global entre Fabric e CometBFT: o START do CometBFT leva o envelope de um destino Fabric (o relay
				// CometBFT → Fabric precisa da assinatura do cliente) e, com -so-cmt, a tx não vai direto ao Fabric
				cmtEnv, soCMT := "", false
				fabAlvo, envAlvo := "", (*common.Envelope)(nil) // o MESMO envelope vai ao Fabric e dentro do START do CometBFT
				if len(targets) > 1 && len(cmtRPC) > 0 {
					temCMT := false
					for _, c := range targets {
						if _, ok := cmtRPC[c]; ok {
							temCMT = true
						} else if fabAlvo == "" {
							fabAlvo = c
						}
					}
					if temCMT && fabAlvo != "" {
						if e, err := builder.build(fabAlvo, txLabel, *flagPayload); err == nil {
							if b, err := proto.Marshal(e); err == nil {
								cmtEnv, envAlvo = base64.StdEncoding.EncodeToString(b), e
							}
						}
						soCMT = txRng.Float64() < *flagSoCMT
					}
				}
				txStart := time.Now()

				var pc *pendingCommit
				if *flagMeasureCommit {
					pc = &pendingCommit{txStart: txStart, done: make(chan time.Duration, 1)}
					pendingCommits.Store(txLabel, pc)
				}

				var txWg sync.WaitGroup
				var errCnt atomic.Int32

				for _, canal := range targets {
					txWg.Add(1)
					semShard <- struct{}{}
					go func(c string) {
						defer txWg.Done()
						defer func() { <-semShard }()

						if url, ok := cmtRPC[c]; ok {
							tx := "L|" + txLabel
							if len(targets) > 1 {
								tx = "S|" + txLabel + "|" + strings.Join(targets, ",")
								if cmtEnv != "" {
									tx += "|" + cmtEnv
								}
							}
							e2eEnvio(c, txLabel)
							if err := cmtBroadcast(url, tx); err != nil {
								fmt.Fprintf(os.Stderr, "cometbft %s: %v\n", c, err)
								errCnt.Add(1)
							}
							return
						}
						if soCMT {
							return // -so-cmt: só o CometBFT recebe; o Fabric recebe pelo relay do coordenador do CometBFT
						}
						env, err := envAlvo, error(nil)
						if c != fabAlvo || env == nil {
							env, err = builder.build(c, txLabel, *flagPayload)
						}
						if err != nil {
							fmt.Fprintf(os.Stderr, "build %s: %v\n", c, err)
							errCnt.Add(1)
							return
						}

						replicaCandidates := *flagCFTReplicas
						if *flagBFTReplicas > replicaCandidates {
							replicaCandidates = *flagBFTReplicas
						}
						candidates := candidateAddrs(router[c], replicaCandidates)
						if cached, ok := lastGoodAddr.Load(c); ok {
							cachedAddr := cached.(string)
							reordered := []string{cachedAddr}
							for _, a := range candidates {
								if a != cachedAddr {
									reordered = append(reordered, a)
								}
							}
							candidates = reordered
						}

						var lastErr error
						for _, addr := range candidates {
							client, err := pool.get(addr)
							if err != nil {
								lastErr = fmt.Errorf("pool.get %s: %w", addr, err)
								continue
							}

							ctx, cancel := context.WithTimeout(context.Background(), time.Duration(*flagRPCTimeout)*time.Second)
							e2eEnvio(c, txLabel) // relógio ponta a ponta: já assinada, antes de abrir o envio
							stream, err := client.Broadcast(ctx)
							if err != nil {
								cancel()
								lastErr = fmt.Errorf("broadcast %s: %w", addr, err)
								continue
							}
							if err := stream.Send(env); err != nil {
								cancel()
								lastErr = fmt.Errorf("send %s: %w", addr, err)
								continue
							}
							reply, err := stream.Recv()
							cancel()
							if err != nil {
								lastErr = fmt.Errorf("recv %s: %w", addr, err)
								continue
							}
							if reply.Status != common.Status_SUCCESS {
								// Inclui o caso "skeen: not coordinator" — tenta o
								// próximo candidato (outra réplica pode ser a
								// coordenadora atual após um failover).
								lastErr = fmt.Errorf("broadcast %s rejected: status=%s", addr, reply.Status)
								continue
							}
							lastGoodAddr.Store(c, addr)
							return
						}
						fmt.Fprintf(os.Stderr, "%v\n", lastErr)
						errCnt.Add(1)
					}(canal)
				}

				txWg.Wait()

				if time.Now().Before(measureStart) || (!measureEnd.IsZero() && time.Now().After(measureEnd)) {
					// Aquecimento (início) ou cooldown (fim): descarta a amostra
					// (sucesso ou erro) das métricas — não conta pra TPS/latência.
					if pc != nil {
						pendingCommits.Delete(txLabel)
					}
					return
				}

				if errCnt.Load() != 0 {
					if pc != nil {
						pendingCommits.Delete(txLabel)
					}
					recorder.errors.Add(1)
				} else if pc == nil {
					recorder.record(id, time.Since(txStart), isCross)
					if *flagPinShards && !isCross {
						shardOK[targets[0]].Add(1)
					}
				} else {
					select {
					case d := <-pc.done:
						recorder.record(id, d, isCross)
					case <-time.After(time.Duration(*flagCommitTimeout) * time.Second):
						pendingCommits.Delete(txLabel)
						fmt.Fprintf(os.Stderr, "[measure-commit] timeout esperando commit de %s\n", txLabel)
						recorder.errors.Add(1)
					}
				}
			}(id)
		}

	}
	if *flagPinShards {
		if *flagDuration <= 0 || *flagCrossProb != 0 {
			fmt.Fprintln(os.Stderr, "-pin-shards exige -duration > 0 e -cross 0")
			os.Exit(1)
		}
		perShard := (*flagWorkers + len(allShards) - 1) / len(allShards)
		var loopWg sync.WaitGroup
		for k, c := range allShards {
			loopWg.Add(1)
			go func(k int, c string) {
				defer loopWg.Done()
				loop(c, make(chan struct{}, perShard), (k+1)*1000000000)
			}(k, c)
		}
		loopWg.Wait()
	} else {
		loop("", sem, 0)
	}

	wg.Wait()
	effectiveEnd := time.Now()
	if !measureEnd.IsZero() && measureEnd.Before(effectiveEnd) {
		effectiveEnd = measureEnd
	}
	recorder.printReport(effectiveEnd.Sub(measureStart).Seconds())
	e2eDump()
	if *flagPinShards {
		secs := effectiveEnd.Sub(measureStart).Seconds()
		for _, c := range allShards {
			fmt.Printf("PERSHARD %s tps=%.0f\n", c, float64(shardOK[c].Load())/secs)
		}
	}
}

// =============================================================================
// Latência PONTA A PONTA até o commit (2026-09-29): com SKEEN_TXTIMING_DIR_CLIENTE definido, o cliente anota
// o instante de envio (já assinada, antes de abrir o Broadcast) das tx da MESMA amostra do txtiming do
// servidor (FNV-64a do TxId, 1 a cada SKEEN_TXTIMING_SAMPLE) e grava no fim "S,<canal>,<txid>,<ns>" em
// <dir>/txtiming_cliente_<pid>.csv. Cliente e orderer rodam na mesma máquina (mesmo relógio): latência
// ponta a ponta = commit no servidor - envio no cliente, pegando a fila de transporte/TLS/gRPC que o
// registro no Broadcast não vê. A assinatura feita pelo próprio cliente (custo do gerador) fica de fora.
// =============================================================================

var (
	e2eDir    = os.Getenv("SKEEN_TXTIMING_DIR_CLIENTE")
	e2eSample = func() uint64 {
		if n, err := strconv.Atoi(os.Getenv("SKEEN_TXTIMING_SAMPLE")); err == nil && n > 0 {
			return uint64(n)
		}
		return 32
	}()
	e2eMu   sync.Mutex
	e2eRecs []string
)

func e2eEnvio(canal, txID string) {
	if e2eDir == "" {
		return
	}
	h := fnv.New64a()
	h.Write([]byte(txID))
	if h.Sum64()%e2eSample != 0 {
		return
	}
	linha := fmt.Sprintf("S,%s,%s,%d\n", canal, txID, time.Now().UnixNano())
	e2eMu.Lock()
	e2eRecs = append(e2eRecs, linha)
	e2eMu.Unlock()
}

func e2eDump() {
	if e2eDir == "" {
		return
	}
	e2eMu.Lock()
	defer e2eMu.Unlock()
	final := fmt.Sprintf("%s/txtiming_cliente_%d.csv", e2eDir, os.Getpid())
	f, err := os.Create(final + ".tmp")
	if err != nil {
		return
	}
	for _, l := range e2eRecs {
		f.WriteString(l)
	}
	f.Close()
	os.Rename(final+".tmp", final)
}
