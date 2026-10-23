# Lume 1.7 — instalador unificado validado

**Histórico:** substituído pelo [instalador 1.7.1](INSTALADOR-1.7.1.md), que usa
FFmpeg próprio e inclui fontes/avisos no mesmo EXE. As prévias descritas abaixo
foram preservadas em `.cache/installer-development-artifacts`; não são a entrega atual.

Estado em 21/10/2026: instalador completo gerado e aplicado neste computador.
A instalação permanente está em `%LOCALAPPDATA%\Programs\LumeDownloads`.
O atalho **Lume Downloads** da Área de Trabalho e do menu Iniciar aponta a versão
1.7; `start.cmd` do projeto também dá preferência a essa instalação padrão.
O pacote portátil 1.6 foi preservado. A fila e os downloads não são copiados para
os pacotes nem modificados pelo instalador.

## O que agora acontece junto

- Instalação do aplicativo e do runtime Python, sem exigir Python instalado.
- Inclusão privada de Node.js 24.22.1 e FFmpeg/ffprobe n9.0.2, sem npm/PATH global.
- Registro da ponte Native Messaging de Chrome/Edge para o usuário atual.
- Atalhos, entrada de desinstalação e tela Conectar navegadores.
- Reinstalação com backup para reparar arquivos ausentes ou danificados.
- Desinstalação independente do runtime instalado; preservação de dados externos.

## Evidência executada

340 testes Python na suíte final, com 1 ignorado por privilégio de symlink e
nenhum erro (268,116 s). Junctions reais têm cobertura. A rotina transitória de
copiar o runtime para desinstalar foi removida após provar o helper independente.

O instalador completo passou por instalação e reinstalação reais em caminho com
espaço/acentuação, conferência do inventário inteiro, quatro views HKCU da ponte,
atalhos via COM e entrada de desinstalação. Reparou o próprio host ausente.
O desinstalador funcionou com host e DLL Python ausentes, removeu dados próprios
e preservou arquivo externo/backups. O teste restaurou exatamente a configuração
anterior; depois o pacote foi aplicado à pasta permanente em 37,782 s.
Esses tempos de instalação não são um benchmark de velocidade de download.

Na instalação permanente, 14 verificações reais do pacote passaram com PATH vazio:
interface principal/seis janelas e limites dos botões, recursos JavaScript de
mídia, caminhos privados dos runtimes, HTTP paralelo, hashes, protocolo nativo,
fila persistente/confirmada, TLS inválido recusado e HLS/DASH com vídeo e áudio.
O download de 28 MiB foi concluído com 8 conexões, SHA-256 conferido e temporários
removidos. O motor de download 1.6 foi preservado; não há nova comparação com IDM.

Abertura automática também foi testada: chamada real ao host instalado com origem
de extensão abriu a interface, mostrou a confirmação e somente após o clique
em Baixar agora transferiu 2 MiB. Hash independente igual à fila e à fixture:
`91d3beb88a9b2f778a6c44a1c53b63d3c79931845a9aef84b3fb414610bd1938`.
Esse teste exercita host/app; NÃO equivale ao envio pelos menus reais de Chrome/Edge.
A ferramenta não opera os menus/popup das extensões, e Edge não está conectado à
sua automação. A rodada real dos dois navegadores permanece pendente.

## Entrega e pendências reais

Instalador: `dist/LumeSetup-1.7.0-windows-x64.exe` (164.688.765 bytes).
SHA-256: `0030f17b067b9708d19a3ba1e41b5e64292cf28f4ed09fe2c468be02d24f1f6b`.
O arquivo não possui assinatura Authenticode de produção. O hash comprova bytes
contra referência confiável, não autentica por si só o editor.

A publicação da extensão nas lojas permanece pendente. O Chrome exige canal da
loja para o fluxo externo suportado no Windows e a aprovação do usuário. Assim,
esta entrega local ainda usa Carregar sem compactação em cada navegador novo;
o programa já registra a ponte automaticamente. Nada foi publicado, forçado por
política ou alterado em perfis de navegador. Preparação e requisitos do editor
estão em PUBLICACAO-EXTENSAO.md; o ZIP 0.3.2 é de revisão, não uma publicação.

O instalador completo está validado para uso local autorizado. Antes do repasse
a terceiros, ainda está sendo concluído o pacote de fontes/avisos das bibliotecas
ligadas ao FFmpeg. Não foi declarada liberação dessa distribuição. Hospedagem,
identidade de assinatura de produção e validação numa VM Windows limpa também
não foram configuradas/comprovadas.

## Recibos locais

- `../.cache/lume-installer/applied.json`
- `.cache/installer-complete-tests.txt`
- `.cache/installer-release.json`
- `.cache/installer-installed-verification.txt` e `.cache/package-verification.json`
- `../.cache/lume-installer/run-ff96c794997a4d92a0524ab9aca55db7/result.json`
- `../.cache/lume-installer/gui-077f6c7e955d442badaf3c42100daf95/result.json`
- `../.cache/lume-installer/installed-gui-download.png` e `integration-center.png`

Nenhum commit, push, publicação, conta de editor ou chave de produção foi criado.
