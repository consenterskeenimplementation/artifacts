package main

// skeen-node: um validador de uma blockchain CometBFT com a aplicação Skeen no mesmo processo (cliente ABCI local).
// Uso: skeen-node --home <dir do nó> --skeen-config <skeen.json> [--out-dir <dir das entregas e do rastro>]
import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	cfg "github.com/cometbft/cometbft/config"
	cmtflags "github.com/cometbft/cometbft/libs/cli/flags"
	cmtlog "github.com/cometbft/cometbft/libs/log"
	nm "github.com/cometbft/cometbft/node"
	"github.com/cometbft/cometbft/p2p"
	"github.com/cometbft/cometbft/privval"
	"github.com/cometbft/cometbft/proxy"
	"github.com/spf13/viper"
)

func main() {
	home := flag.String("home", "", "diretório do nó (config/ e data/)")
	scfg := flag.String("skeen-config", "", "configuração do Skeen (grupos, réplicas, chaves, coordenadores)")
	outDir := flag.String("out-dir", "", "onde gravar a sequência de entregas e o rastro de eventos desta réplica")
	flag.Parse()
	if *home == "" {
		fmt.Fprintln(os.Stderr, "falta --home")
		os.Exit(2)
	}
	config := cfg.DefaultConfig()
	config.SetRoot(*home)
	viper.SetConfigFile(filepath.Join(*home, "config", "config.toml"))
	if err := viper.ReadInConfig(); err != nil {
		fatal("lendo config: %v", err)
	}
	if err := viper.Unmarshal(config); err != nil {
		fatal("decodificando config: %v", err)
	}
	config.SetRoot(*home)
	if err := config.ValidateBasic(); err != nil {
		fatal("config inválida: %v", err)
	}
	pv := privval.LoadFilePV(config.PrivValidatorKeyFile(), config.PrivValidatorStateFile())
	nodeKey, err := p2p.LoadNodeKey(config.NodeKeyFile())
	if err != nil {
		fatal("chave do nó: %v", err)
	}
	logger := cmtlog.NewTMLogger(cmtlog.NewSyncWriter(os.Stdout))
	logger, err = cmtflags.ParseLogLevel(config.LogLevel, logger, cfg.DefaultLogLevel)
	if err != nil {
		fatal("nível de log: %v", err)
	}
	gen, err := nm.DefaultGenesisDocProviderFunc(config)()
	if err != nil {
		fatal("gênese: %v", err)
	}
	raw, err := os.ReadFile(*scfg)
	if err != nil {
		fatal("lendo %s: %v", *scfg, err)
	}
	var sc Config
	if err := json.Unmarshal(raw, &sc); err != nil {
		fatal("decodificando %s: %v", *scfg, err)
	}
	app := NewSkeenApp(sc.Chain, logger.With("module", "skeen"))
	sn, err := NewNet(sc, pv.Key.PrivKey, *outDir) // votos assinados com a chave de validador do CometBFT
	if err != nil {
		fatal("skeen: %v", err)
	}
	sn.app, app.net = app, sn
	if err := sn.listen(); err != nil {
		fatal("skeen: escutando em %s: %v", sc.Listen, err)
	}
	if sn.coordinator() && len(sc.WireListen) > 0 { // ponte com o Fabric (Skeen entre plataformas)
		if err := sn.listenWire(); err != nil {
			fatal("skeen: fio do Fabric: %v", err)
		}
	}
	go sn.retransmit()
	if sn.coordinator() {
		go sn.batchLoop()
	}
	logger.Info("skeen", "blockchain", sc.Chain, "chain_id", gen.ChainID, "replica", sn.me, "coordenador", sn.coordinator())
	node, err := nm.NewNode(config, pv, nodeKey, proxy.NewLocalClientCreator(app),
		nm.DefaultGenesisDocProviderFunc(config), cfg.DefaultDBProvider, nm.DefaultMetricsProvider(config.Instrumentation), logger)
	if err != nil {
		fatal("criando nó: %v", err)
	}
	if err := node.Start(); err != nil {
		fatal("iniciando nó: %v", err)
	}
	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt, syscall.SIGTERM)
	<-c
	_ = node.Stop()
	node.Wait()
}

func fatal(f string, a ...any) {
	fmt.Fprintf(os.Stderr, f+"\n", a...)
	os.Exit(1)
}
