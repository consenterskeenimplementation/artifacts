#!/usr/bin/env python3
"""Gera o configtx.yaml do teste de WAN a partir do configtx.yaml do projeto: nos perfis por canal (<Base><s>), o
Host dos consenters passa a ser o IP do local do canal s, e os caminhos crypto-config/ passam a apontar para o
material da WAN. Uso: gera_configtx_wan.py <configtx.yaml> <dir crypto WAN> <modelo de IP, ex. 10.10.{s}.1> > saida"""
import re
import sys

PERFIS = ("SkeenCFTRealGenesis", "SkeenBFTRealGenesis")
PERFIL = re.compile(r"^    ([A-Za-z]+?)(\d+):")

origem, cripto, modelo = sys.argv[1:4]
cripto = cripto.rstrip("/") + "/"
canal = None
for linha in open(origem, encoding="utf-8"):
    m = PERFIL.match(linha)
    if m:
        canal = int(m.group(2)) if m.group(1) in PERFIS else None
    elif re.match(r"^    \S", linha):
        canal = None
    if canal is not None:
        linha = linha.replace("Host: 127.0.0.1", "Host: " + modelo.format(s=canal))
    sys.stdout.write(linha.replace("crypto-config/", cripto))
