# Re:ZERO -Perfect Run- — Legendas PT-BR & Extensão (`60/60` Completo)

Coleção completa de legendas em **Português do Brasil (PT-BR)** e extensão Chrome/Edge/Brave/Opera (Manifest V3) para assistir a todos os **60 episódios** (Arcs 01–06 + OVA *Memory Snow*) no **[rezeroperfect.run](https://rezeroperfect.run/)**, com karaokê Romaji, placas/notas (*signs*), personalização visual e sincronização automática via GitHub.

> **For the `rezeroperfect.run` team:** All **60/60** final Brazilian Portuguese (PT-BR) subtitle files (Arcs 01–06 + *Memory Snow*) ready for site integration are located in [`translated_ptbr/`](translated_ptbr/) (`.ass` with original styles, typesetting, and karaoke preserved + `.vtt`), and terminology standardization is documented in [`GLOSSARIO.md`](GLOSSARIO.md).

---

## Catálogo Completo (`60/60` Episódios)

| Arco | Episódios | Cobertura do Anime | Status |
| :--- | :---: | :--- | :---: |
| **Arc 01** — *The Tumultuous First Day* | `2/2` | S1 `[1–3]` | ✅ Completo |
| **Arc 02** — *The Turbulent Week* | `5/5` | S1 `[4–11]`, OVA *Memory Snow* | ✅ Completo |
| **Arc 03** — *Return to the Royal Capital* | `9/9` | S1 `[12–25]`, S2 `[1]` | ✅ Completo |
| **Arc 04** — *The Everlasting Contract* | `17/17` | S2 `[2–25]` | ✅ Completo |
| **Arc 05** — *The Stars That Engrave History* | `15/15` | S3 `[1–16]` | ✅ Completo |
| **Arc 06** — *The Corridor of Memories* | `12/12` | S4 `[1–19]` | ✅ Completo |
| **Total** | **`60/60`** | **Catálogo completo (`26.351` legendas)** | **✅ 100%** |

---

## 1. Como Instalar e Usar a Extensão (Chrome, Edge, Brave, Opera)

### Passo a passo
1. **Baixe o projeto**: clique em **Code → Download ZIP** aqui no GitHub e extraia a pasta no seu computador (ou clone o repositório com `git clone`).
2. Abra a página de extensões do seu navegador:
   - **Chrome**: `chrome://extensions`
   - **Edge**: `edge://extensions`
   - **Brave**: `brave://extensions`
   - **Opera**: `opera://extensions`
3. Ative o **Modo do desenvolvedor** (*Developer mode*), no canto superior direito.
4. Clique em **Carregar sem compactação** (*Load unpacked*) e selecione a pasta **`extension/`** dentro do projeto extraído.
5. Acesse **[rezeroperfect.run](https://rezeroperfect.run/)**, abra qualquer arco (ex.: `/arc-1`) e dê play no episódio!

### Dica importante no player do site
- Deixe as legendas nativas do site em inglês (**Subtitles / CC**) **desligadas** para evitar sobreposição. Caso elas estejam ativadas, o popup da extensão exibe um alerta com botão de **1 clique** (*Desligar legenda EN e recarregar*).

### Recursos e atalhos da extensão
- **Catálogo embutido + atualização automática via GitHub**: todas as 60 legendas já vêm prontas na extensão e correções futuras podem ser baixadas pelo botão **Sincronizar agora** no popup.
- **Exibição fiel**: suporte a **placas/notas (*signs*)**, **karaokê Romaji nas OP/ED** (ambos opcionais no popup), modo janela e **tela cheia**.
- **Personalização em tempo real**: ajuste de **tamanho da fonte** (`70%–160%`), **opacidade do fundo** (`0%–100%`), **altura inferior** (`2%–20%`) e **sincronia** (`-5000 ms` a `+5000 ms`).
- **Carregador local de legendas**: teste ou sobrescreva qualquer episódio carregando um arquivo `.ass`, `.ssa`, `.srt`, `.vtt` ou `.json` direto pelo popup.
- **Atalhos de teclado no player**:
  - `Alt + S` — ligar/desligar legendas PT-BR
  - `Alt + [` — adiantar sincronia em `-100 ms`
  - `Alt + ]` — atrasar sincronia em `+100 ms`

---

## 2. Usar as Legendas Fora da Extensão (VLC, mpv, Site)

Os arquivos finais prontos para qualquer player local ou integração web estão em [`translated_ptbr/`](translated_ptbr/):
- **`arcXX-epYY.ass`** — formato ASS completo com estilos, posicionamento, placas e karaokê originais preservados (recomendado para **mpv** / **VLC**).
- **`arcXX-epYY.vtt`** — formato WebVTT limpo para players HTML5.

Todas as escolhas de nomes, títulos, magias e vozes dos personagens seguem [`GLOSSARIO.md`](GLOSSARIO.md).

---

## 3. Estrutura do Repositório

```text
rezero-perfect-ptbr/
├── GLOSSARIO.md                  # Padronização de nomes, termos e vozes dos personagens (Arcs 01–06)
├── README.md                     # Este guia
├── extension/                    # Extensão Manifest V3 pronta para instalar no navegador
│   ├── manifest.json             # v1.0.0
│   ├── background/service-worker.js
│   ├── content/                  # Parser (.ass/.srt/.vtt) + overlay sincronizado no player
│   ├── icons/
│   ├── popup/                    # Interface de controles, aparência, sync GitHub e carga local
│   └── subtitles/                # 60 JSONs PT-BR otimizados + manifest.json do catálogo
├── translated_ptbr/              # 60 arquivos finais .ass + 60 arquivos .vtt prontos para uso
├── raw_en/                       # (Local / gitignored) Legendas EN base usadas ao recompilar
└── scripts/
    ├── sub_pipeline.py           # CLI: status, inspect, extract, prepare, context, import-context, validate, build, export-release
    ├── mkv_subtitle_extractor.py # Extrator EBML/Matroska em Python puro (+ fallback mkvextract/ffmpeg)
    ├── generate_icons.py         # Gerador dos ícones da extensão
    ├── test_pipeline.py          # Testes unitários Python (unittest)
    └── test_extension_parser.mjs # Testes unitários do parser JS da extensão (Node)
```

---

## 4. Manutenção, Revisão de Legendas e Pipeline (Desenvolvedores)

Requisitos: **Python 3.10+** (e **Node.js 18+** para rodar os testes da extensão).

### Conferir status do catálogo
```powershell
python scripts/sub_pipeline.py status
```

### Editar ou revisar legendas em contexto contínuo
Para corrigir falas mantendo a concordância entre legendas consecutivas (`legenda 1 / legenda 2`):
```powershell
# 1. (Opcional) Extrair novamente de um .mkv caso não tenha a fonte local em raw_en/
python scripts/sub_pipeline.py extract caminho/do/episodio.mkv --arc arc04 --ep 1 --delete-source
python scripts/sub_pipeline.py prepare --arc arc04 --ep 1

# 2. Gerar/atualizar o arquivo de blocos contínuos (translated_ptbr/work/<ep>/script_context.txt)
python scripts/sub_pipeline.py context --arc arc04 --ep 1

# 3. Após editar as linhas PT: em script_context.txt, importar de volta nos chunks
python scripts/sub_pipeline.py import-context --arc arc04 --ep 1

# 4. Validar placeholders/tags e recompilar (.ass, .vtt, JSON da extensão e manifest.json)
python scripts/sub_pipeline.py validate --arc arc04 --ep 1
python scripts/sub_pipeline.py build --arc arc04 --ep 1
```

### Gerar pacotes `.zip` de distribuição (`dist/`)
```powershell
python scripts/sub_pipeline.py export-release
```
Gera em `dist/` (pasta ignorada pelo Git):
- `rezero-perfect-ptbr-extensao-v1.0.0.zip` — pacote pronto da extensão.
- `rezero-perfect-ptbr-legendas-ass-vtt.zip` — pacote com todos os `.ass`, `.vtt` e `GLOSSARIO.md`.

### Rodar os testes automatizados
```powershell
python -m unittest discover -s scripts -p "test_*.py" -v
node --test scripts/test_extension_parser.mjs
```
