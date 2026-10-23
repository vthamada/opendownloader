# Instalação e entrega local

O fluxo da versão 1.13.0 é um instalador Windows por usuário: `OpenDownloaderSetup-1.13.0-windows-x64.exe`.
Ele instala o aplicativo em uma pasta permanente, cria atalhos e entrada de desinstalação
e registra a ponte de Chrome/Edge na mesma operação. Não exige comandos PowerShell
do usuário. O assistente final e o menu **Navegadores → Conectar Chrome e Edge**
reúnem as opções de conexão. O estado das verificações está em `VALIDACAO.md`;
gerar um EXE não comprova sua instalação.

## Procedimento em outro computador

1. Confira o SHA-256 do instalador com uma referência confiável e execute o EXE.
2. Confirme a pasta e conclua o assistente. A pasta padrão é
   `%LOCALAPPDATA%\Programs\LumeDownloads`. Não é necessário instalar Python.
3. Na tela **Conectar navegadores**, configure a extensão uma vez em cada navegador.
   Selecione a aba Chrome ou Edge. O assistente mostra a pasta da extensão, permite
   copiar seu caminho e solicitar a abertura do gerenciador do navegador correto.
   Ative **Modo do desenvolvedor**, escolha **Carregar sem compactação** e cole o
   caminho. Esse passo é feito uma vez por navegador. Se o navegador não for
   localizado, use o endereço interno exibido pelo assistente.
   A publicação nas lojas permanece suspensa. Não há instalação forçada nem
   modificação de perfis ou configurações de segurança do navegador.
4. No popup, use **Verificar conexão com o aplicativo**. Depois envie um arquivo
   conhecido com o OpenDownloader fechado, confirme as opções e confira a conclusão. Repita
   no outro navegador. Diagnóstico do host não substitui transferência real.

O registro da ponte e os atalhos são feitos pelo instalador. Reinstalar na mesma
pasta permite reparar arquivos ausentes/danificados e guarda a pasta anterior
como backup. A fila e os downloads ficam fora da instalação. A desinstalação
remove apenas dados comprovadamente próprios; modificações, arquivos desconhecidos
e backups permanecem. Se o desregistro não puder terminar, o host é retido e a
remoção retorna pendência em vez de anunciar sucesso.

## Componentes de mídia

O usuário autorizou em 21/10/2026 a exceção para os componentes privados. O
instalador completo inclui Python, Node.js 24.22.1 e FFmpeg/ffprobe no próprio OpenDownloader,
sem PATH, instalação global, npm ou alteração de programas existentes. O build
confere inventário/hash de todos os arquivos e executa as verificações do pacote
com PATH vazio antes de compilar. Não é necessário instalar esses programas à parte.
A versão 1.7.2 substitui o FFmpeg de terceiros por uma compilação própria da fonte
exata, sem bibliotecas externas e com LGPL2.1+. Fontes, receita, configuração e
avisos acompanham o mesmo instalador, em `app\runtimes\ffmpeg\legal`. Não são
componentes para instalar separadamente. A revisão técnica não encontrou um gap
documental concreto nesse conjunto; isso não equivale a um parecer jurídico.
DLLs/EXEs compatíveis recompilados podem ser substituídos com o aplicativo fechado;
não há bloqueio de hash em execução. O reparo preserva alterações em backup.
A 1.7.0 anterior é histórica e não deve ser escolhida para repasse a terceiros.
Veja `THIRD_PARTY_NOTICES.md` e o relatório 1.7.1.

## Publicação e confiança

A entrega local não configura hospedagem, feed de atualização ou chave de produção.
O SHA-256 detecta alteração quando comparado com referência confiável, mas não
autentica o editor se arquivo e referência forem substituídos juntos. Não há
assinatura Authenticode de produção. O verificador Ed25519 continua disponível
para releases futuros com identidade controlada pelo usuário.

Para a experiência de loja, falta publicar a extensão nas contas do editor e
confirmar os IDs/canais. No Windows, a instalação externa suportada do Chrome usa
a Chrome Web Store e pede confirmação do usuário. O IDM também documenta instalação
automática com fallback para link direto da loja. Fontes oficiais:

- https://www.internetdownloadmanager.com/register/new_faq/chrome_extension.html
- https://www.internetdownloadmanager.com/register/new_faq/edge_extension.html
- https://developer.chrome.com/docs/extensions/how-to/distribute/install-extensions

## Pacote portátil anterior

O ZIP 1.6 e seu SHA-256 continuam preservados. Nesse formato, manter EXEs e
`_internal` juntos continua necessário. A configuração manual anterior é uma
alternativa de diagnóstico; o instalador 1.7 passa a ser o fluxo principal
quando sua validação e seus componentes estiverem concluídos. Nenhuma base de fila,
token ou credencial é transferida no pacote.
