# OpenDownloader 1.7.2

O usuário escolheu **OpenDownloader** como novo nome do produto. A extensão
correspondente é **0.3.3**. Não houve publicação em lojas nem alteração de conta.

## Mudança e compatibilidade

O nome aparece nas janelas, cabeçalho, Sobre, mensagens, menu e popup da extensão,
assistente de instalação, atalhos e lista de aplicativos do Windows.
O instalador tem o nome `OpenDownloaderSetup-1.7.2-windows-x64.exe`.

Continuam inalterados o módulo Python `lume`, os executáveis internos
`LumeDownloads.exe`/`LumeNativeHost.exe`, a pasta de instalação
`%LOCALAPPDATA%/Programs/LumeDownloads`, a pasta de dados
`%LOCALAPPDATA%/LumeDownloads`, as identidades de propriedade e o host
`app.lume.downloads`. A chave e o ID da extensão local
`meicfjedeegbopmeifconaiekbmppbai` também são mantidos. São contratos de
compatibilidade; não é necessário mover a fila ou recriar a ponte.

Atalhos antigos só são removidos quando o recibo da instalação comprova seus
bytes e caminhos. Uma falha restaura os atalhos anteriores; modificações externas
são preservadas e impedem a substituição. O backup da aplicação anterior continua
sob a propriedade do instalador.

## Validação

Aplicado neste computador. A fila foi preservada byte a byte e os atalhos foram
migrados. Passaram 77 testes Python (um ignorado), uma regressão adicional,
cinco suítes Node e 14 verificações do pacote. O aplicativo instalado passou
também no self-test das seis janelas com PATH vazio.

Relatório final: `dist/OpenDownloaderSetup-1.7.2-VALIDACAO.md`.
SHA-256 do instalador: `7b6cd21db4397d82846bccece367566aaf83713fd31541bc2d85b2004cdaaa6b`.
O histórico em `VALIDACAO.md` corresponde às versões anteriores; não comprova
os testes desta entrega. O teste direto do host também não comprova um clique
no menu real de Chrome ou Edge.

## Distribuição

A publicação e o anúncio da extensão pelo instalador continuam pendentes.
A mudança de nome não cria uma listagem na loja nem atesta disponibilidade de marca.
Os componentes privados de mídia seguem os mesmos arquivos e hashes aprovados
na entrega 1.7.1. Não há nova instalação global de runtimes ou alteração do PATH.
