# Privacidade do OpenDownloader — rascunho para revisão do editor

Este texto descreve o código da extensão preparada 0.5.0 e do aplicativo local.
Não foi publicado como política oficial. Identidade do responsável, contato,
URL pública e revisão final permanecem pendentes.

O OpenDownloader envia ao aplicativo instalado no computador os links que o usuário escolhe
e os metadados necessários, como título e tipo de mídia. A comunicação com o
aplicativo usa Native Messaging local. A extensão não possui serviço de telemetria,
cadastro de usuário ou servidor remoto do OpenDownloader configurado.

A procura de mídia ocorre mediante ação do usuário ou ativação opcional por site.
Ela examina a página/frames autorizados para encontrar recursos compatíveis; não
solicita permissão para ler cookies nem histórico completo de navegação. A extensão
armazena preferências, sites autorizados e o último resultado de envio localmente.
Candidatos e permissões temporárias da procura ficam no armazenamento de sessão.

Ao escolher **Gravar reprodução**, o usuário seleciona uma aba, janela ou tela
no seletor do navegador. Somente essa superfície é capturada durante a sessão;
áudio depende do compartilhamento escolhido. O microfone não é solicitado.
O vídeo e áudio capturados seguem em blocos pelo Native Messaging para o mesmo
computador. A extensão não envia esses dados a um servidor do OpenDownloader.
O aplicativo conserva a captura, o título informado e o recibo local em
`recordings` na pasta de dados. Originais e parciais permanecem até a pessoa
arquivar ou removê-los; cancelar um pedido da fila não apaga esses arquivos.
O usuário pode pausar, encerrar ou revogar o compartilhamento pelo navegador.

O aplicativo conserva URLs completas e metadados na fila local para permitir
retomada. Isso pode incluir parâmetros temporários que façam parte do endereço.
Ele acessa os servidores das URLs escolhidas para consultar formatos e transferir
arquivos. Essas conexões seguem o funcionamento normal de HTTP/HTTPS, podendo
enviar aos servidores de origem informações necessárias à requisição. O OpenDownloader não
promete ocultar o endereço de rede do usuário perante a fonte do download.

Permissões automáticas por site podem ser revogadas pela extensão/navegador.
Desinstalar o aplicativo preserva a fila e os arquivos baixados por padrão;
portanto a desinstalação não é uma solicitação de apagar esses dados. O editor
deve definir e comunicar o procedimento de exclusão e seu canal de atendimento
antes da publicação da política.
