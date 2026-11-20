# Re:ZERO -Perfect Run- — Legendas PT-BR & Extensão

Pipeline episódio por episódio (baixo uso de disco) e extensão Chrome/Edge Manifest V3 para assistir ao **[rezeroperfect.run](https://rezeroperfect.run/)** com legendas em Português do Brasil (PT-BR).

> **Para a equipe do `rezeroperfect.run`:** os arquivos finais de legenda em Português (BR) prontos para integração estão em [`translated_ptbr/`](translated_ptbr/) (`.ass` com estilos e karaokê preservados + `.vtt`), e a padronização dos termos está documentada em [`GLOSSARIO.md`](GLOSSARIO.md).

---

## Estrutura de Pastas

```text
rezero-perfect-ptbr/
├── GLOSSARIO.md                  # Padronização de nomes, termos e estilo PT-BR
├── README.md
├── extension/                    # Extensão Manifest V3 (Chrome / Edge)
│   ├── manifest.json
│   ├── background/service-worker.js
│   ├── content/
│   │   ├── content.js
│   │   ├── overlay.css
│   │   └── subtitle-parser.js
│   ├── icons/
│   ├── popup/
│   └── subtitles/                # Legendas PT-BR publicadas + manifest.json
├── raw_en/                       # Legendas em inglês extraídas (~100 KB cada)
├── translated_ptbr/              # Arquivos finais (.ass, .vtt) e work/<ep>/chunk_*.json
├── workspace_tmp/                # Pasta temporária para 1 .mkv por vez (apagado após extração)
└── scripts/
    ├── generate_icons.py
    ├── mkv_subtitle_extractor.py # Extrator EBML/Matroska em Python puro (+ fallback mkvextract/ffmpeg)
    ├── sub_pipeline.py           # CLI completa: status, inspect, extract, prepare, validate, build
    ├── test_pipeline.py
    └── test_extension_parser.mjs
```

---

## 1. Instalar a Extensão no Chrome ou Edge

1. Abra `chrome://extensions` (Chrome) ou `edge://extensions` (Edge).
2. Ative o **Modo do desenvolvedor** (canto superior direito).
3. Clique em **Carregar sem compactação** (*Load unpacked*).
4. Selecione a pasta `extension/` dentro deste projeto.
5. Abra `https://rezeroperfect.run/arc-1` e, nas configurações do player do site, deixe **Subtitles: Off** (a própria extensão exibe um aviso com botão de 1 clique caso as legendas em inglês queimadas estejam ligadas).

---

## 2. Fluxo Episódio por Episódio (Baixo Uso de Espaço)

Para não acumular dezenas de gigabytes no disco, processe **1 episódio por vez**:

### Passo 1 — Baixar apenas 1 episódio em `workspace_tmp/`
No site `rezeroperfect.run/arc-N`, baixe o `.torrent` do episódio desejado e selecione apenas o arquivo `.mkv` dele no seu cliente torrent, salvando em `workspace_tmp/`.

### Passo 2 — Inspecionar as faixas de legenda
```powershell
python scripts/sub_pipeline.py inspect workspace_tmp/<arquivo.mkv>
```
Isso mostra em milissegundos as faixas de legenda disponíveis (ex.: legenda para áudio japonês vs. áudio inglês), idioma e codec (`S_TEXT/ASS`, `S_TEXT/UTF8`, etc.).

### Passo 3 — Extrair a legenda e apagar o `.mkv` pesado
```powershell
python scripts/sub_pipeline.py extract workspace_tmp/<arquivo.mkv> --arc arc01 --ep 1 --delete-source
```
- O script extrai as faixas de legenda em `raw_en/` (~50 a 150 KB), escolhe automaticamente a faixa principal em inglês (priorizando áudio japonês), mostra as 5 primeiras falas e **valida** que existem diálogos válidos.
- **Somente se a extração e validação passarem**, `--delete-source` remove o `.mkv` grande de `workspace_tmp/`.

### Passo 4 — Preparar os blocos para tradução
```powershell
python scripts/sub_pipeline.py prepare --arc arc01 --ep 1
```
Gera `translated_ptbr/work/arc01-ep01/meta.json` e os arquivos `chunk_001.json`, `chunk_002.json`... preservando timestamps, estilos e tags ASS (`⟪T0⟫`). Traduza o campo `"pt"` de cada entrada seguindo o `GLOSSARIO.md`.

### Passo 5 — Validar e compilar a legenda PT-BR
```powershell
python scripts/sub_pipeline.py validate --arc arc01 --ep 1
python scripts/sub_pipeline.py build --arc arc01 --ep 1
```
O comando `build`:
- Gera `translated_ptbr/arc01-ep01.ass` (com typesetting intacto) e `translated_ptbr/arc01-ep01.vtt`.
- Publica `extension/subtitles/arc01/ep01.json` e atualiza `extension/subtitles/manifest.json`.

### Passo 6 — Testar ao vivo no navegador
1. Em `chrome://extensions`, clique no botão de **Recarregar** da extensão (ou use o carregador de arquivo local direto no popup da extensão para testar instantaneamente antes mesmo de compilar).
2. Abra `https://rezeroperfect.run/arc-1` e dê play no episódio!

---

## Comandos Úteis & Atalhos na Extensão

- Ver progresso geral dos 60 episódios:
  ```powershell
  python scripts/sub_pipeline.py status
  ```
- **Atalhos no player (extensão):**
  - `Alt + S`: ligar/desligar legendas PT-BR
  - `Alt + [` / `Alt + ]`: ajustar sincronia em `-100 ms` / `+100 ms`
