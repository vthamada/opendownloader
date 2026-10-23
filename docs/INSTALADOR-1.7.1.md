# Lume 1.7.1 — instalação unificada

Estado em 21/10/2026: instalado neste computador em
`C:\Users\DTI\AppData\Local\Programs\LumeDownloads`.
Atalhos da Área de Trabalho/menu Iniciar e a entrada de desinstalação apontam a
versão 1.7.1. Os quatro registros HKCU Chrome/Edge foram conferidos; o host
instalado respondeu `app_version:1.7.1, status:ready`.

## Um instalador para o programa

`dist/LumeSetup-1.7.1-windows-x64.exe` reúne aplicativo, Python, Node.js 24.22.1,
FFmpeg/ffprobe próprios, extensão local, atalhos, registro da ponte, reparo e
remoção. O usuário não precisa instalar Python/Node/FFmpeg nem executar comandos
PowerShell. Não há npm, serviço, driver, PATH global ou substituição de software
existente. As opções de navegador ficam no assistente final e no menu
**Navegadores → Conectar Chrome e Edge**.

Ao atualizar, feche o Lume e execute o instalador. A pasta anterior é preservada
em backup; fila e arquivos baixados ficam fora da instalação. O desinstalador usa
um helper independente e preserva arquivos desconhecidos/modificados. Falha de
remoção da ponte mantém o host e retorna pendência, não sucesso artificial.

O instalador tem 131.679.243 bytes (125,58 MiB).
SHA-256: `9ee2f6fffa0b144b6a119f50392674ff63c88397f115886ca7c0c2b287438e9f`.
O arquivo .sha256 está ao lado. Não há assinatura Authenticode de produção;
SHA comparado com uma referência confiável verifica bytes, não autentica sozinho
o editor. As prévias 1.7.0 e os ZIPs intermediários foram preservados em
`.cache/installer-development-artifacts`; não são a entrega atual.

## Validação executada e alcance

- Suíte Python: 340 testes, 1 ignorado por falta de privilégio de symlink, nenhum
  erro (268,116 s). Foi executada ao consolidar o código do instalador; depois,
  a única alteração Python para 1.7.1 foi VERSION. Junctions reais foram testadas.
- Executáveis 1.7.1: 14 verificações no staging e novamente na instalação
  permanente, com PATH vazio. Incluem seis janelas/limites dos botões, scripts
  EJS, caminhos privados, HTTP, SHA-256, protocolo nativo, confirmação/fila,
  rejeição de TLS inválido e HLS/DASH com faixas de áudio/vídeo.
- Fixture de 28 MiB: conclusão com 8 conexões reutilizadas, hash esperado
  `007508a1d8950111f529abc68ee3b19973ab1b87cbc01625c6f19e75564e06ec`, sem temporários.
- Ciclo NSIS real 1.7.0: instalação, reparo de host ausente e desinstalação com
  host/DLL Python ausentes, preservando dados externos e restaurando registros.
  Na 1.7.1, o mesmo instalador/dono de implantação foi usado para upgrade real;
  inventário completo de 1.146 arquivos, atalhos, ARP e ponte foram conferidos.
  A atualização levou 50,047 s e preservou backup da 1.7.0. Não se repetiu uma
  remoção da instalação permanente do usuário após esse upgrade.
- Abertura automática 1.7.1: host instalado chamado com origem de extensão abriu
  a janela e a confirmação; após clique visual em Baixar agora, 2 MiB concluídos
  com hash independente igual à fila:
  `91d3beb88a9b2f778a6c44a1c53b63d3c79931845a9aef84b3fb414610bd1938`.
  Foi uma fila isolada; nenhuma tarefa foi admitida na fila do usuário.

O último item é prova de host/aplicativo, não de cliques nos menus reais de Chrome
ou Edge. Essa rodada real continua pendente: a ferramenta não opera o menu/popup
da extensão e Edge não está conectado à automação disponível. Não afirmar que
chamada direta do host substituiu o navegador. A fixture antiga foi encerrada.

## FFmpeg próprio e fontes incluídas

O candidato BtbN da 1.7.0 anunciado LGPL foi substituído após a auditoria achar
FFTW GPL efetivamente ligado via Chromaprint. O pacote atual usa a fonte FFmpeg
`c89d6ee1ca848314270155ceb13b1cab3744fc3c`, sem alteração, compilada sem bibliotecas
externas, GPL/nonfree/version3, rede/dispositivos/hardware e encoders. A configuração
e a execução informam LGPL2.1+. W32threads está ativo. Protocolos locais:
file/pipe/concat/concatf. O Lume interpreta HLS/DASH e fornece segmentos locais;
não depende do demuxer DASH/libxml2 nem de rede dentro de FFmpeg.

Cinco casos reais com PATH vazio preservaram os hashes de todos os pacotes
comprimidos: H.264/AAC, HEVC/AAC, VP9/Opus, AV1/AAC e fMP4 fragmentado concatf.
Os oito binários importam somente suas seis DLLs FFmpeg e DLLs de sistema;
imports diretos/delay foram conferidos, sem dependência ausente ou FFTW.

O ZIP exato da fonte, receita de recomposição, configuração, LGPL, crédito IJG,
avisos MinGW e exceção GCC estão DENTRO do instalador, em
`app\runtimes\ffmpeg\legal`. A revisão conferiu os arquivos e seus hashes;
nenhum gap documental concreto foi identificado nesse conjunto técnico. Não é
um parecer jurídico ou prova de recompilação bit a bit. A fonte foi comparada
integralmente ao ZIP: 10.424 arquivos, sem diferenças. O compilador portátil foi
usado só no cache e não acompanha o programa.

## O que ainda depende de outra etapa

A extensão 0.3.2 e suas permissões não mudaram. Neste computador o registro já foi
atualizado. Em um computador novo, a entrega local ainda pede **Carregar sem
compactação** uma vez em Chrome/Edge. Registrar o host não instala/ativa extensão.
O modelo de loja do IDM será alcançado após publicação e confirmação dos IDs e
canais. Não foram criadas contas, pagas taxas, publicadas extensões nem alteradas
políticas/perfis de navegador. Pacote de revisão e textos preparados em
PUBLICACAO-EXTENSAO.md e PRIVACIDADE-EXTENSAO-RASCUNHO.md.

Também não foram configurados hospedagem/atualização automática ou identidade de
assinatura de produção; não houve teste em VM Windows limpa. O motor de download
foi preservado e os testes de concorrência passaram; não se repetiu o benchmark
real com IDM/episódio nesta etapa de instalação. Não há promessa de todo site,
DRM, conversão arbitrária, live ou protocolo não implementado.

Fontes oficiais do fluxo de distribuição:
- https://www.internetdownloadmanager.com/register/new_faq/chrome_extension.html
- https://www.internetdownloadmanager.com/register/new_faq/edge_extension.html
- https://developer.chrome.com/docs/extensions/how-to/distribute/install-extensions

## Recibos e provas locais

- `../.cache/lume-installer/applied-1.7.1.json`
- `.cache/installer-release.json` e `.cache/package-verification.json`
- `.cache/installer-1.7.1-installed-verification.txt`
- `.cache/installer-complete-tests.txt`
- `../.cache/lume-installer/gui-cd90ea6399db4876a5c732468c55086d/result.json`
- `../.cache/lume-installer/installed-1.7.1-download.png`
- `../.cache/lume-installer/ffmpeg-clean/RESULTADO.md`, selected-files.json,
  validation-result.json e build-metrics.json
- `../.cache/lume-installer/dependencies/license-review.md` (candidato histórico)

Sem commit, push ou publicação externa. Documentos e validações 1.7.0 são históricos;
este relatório acompanha a entrega 1.7.1 e identifica seu hash exato.
