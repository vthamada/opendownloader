# OpenDownloader — integração opcional com Chrome e Edge — extensão 0.6.0

Na gravação, **Escolher tela ou aba** abre o seletor do navegador. Depois ajuste
o recorte na prévia por arraste ou campos em pixels e clique **Gravar área selecionada**.
**Superfície inteira** mantém a captura sem canvas. Mantenha a aba de gravação
visível durante um recorte, para reduzir efeitos da suspensão do navegador.
Detalhes e limites: [OpenDownloader 1.13.0](../docs/OPENDOWNLOADER-1.13.0.md).

A marca pública é **OpenDownloader**. Os identificadores internos `app.lume.downloads`, a chave pública e os nomes legados de executáveis/pastas são preservados para manter a integração existente e o mesmo ID da extensão.

A extensão envia somente o link escolhido para `app.lume.downloads`. O host
grava o pedido e solicita a abertura do aplicativo para confirmar nome, destino
e opções. A resposta da ponte significa “pedido recebido”, não transferência
iniciada ou concluída. A opção de início automático é controlada no aplicativo.
URLs que exigem a sessão do navegador podem não funcionar no gerenciador.

**Verificar conexão com o aplicativo** consulta a versão do host local somente
após seu clique. Não cria perfil, pedido ou download e não abre a janela do OpenDownloader.
O resultado distingue o erro do navegador (por exemplo, `host not found`) de uma
resposta incompatível. A indicação de ponte respondendo precisa ser complementada
por um envio real, confirmação no aplicativo e verificação do arquivo final.
Essa consulta não substitui o último resultado de envio salvo no popup.

O menu **Baixar com OpenDownloader** é conferido na instalação, no início do navegador e
ao abrir o popup. Um item existente é atualizado sem removê-lo; um item ausente
é recriado. O popup mostra **Menu de links configurado** após a confirmação da
API, ou o erro retornado pelo navegador. Esse aviso não comprova a exibição do
menu nem a conclusão de um download. Se o menu não aparecer no Chrome, confira
que esta versão está ativa e abra o popup uma vez antes de tentar novamente.

## Vídeos e áudios da página

- Abra o botão OpenDownloader e escolha **Procurar nesta página**. O navegador concede
  acesso temporário pela ação do usuário; os players recebem botões de envio.
- Para receber esses botões automaticamente em outras visitas, escolha
  **Ativar botões neste site** e aceite a permissão daquele endereço.
- O popup lista os candidatos encontrados e envia o escolhido para a confirmação
  no aplicativo. Também há **Baixar com OpenDownloader** no clique direito de vídeo/áudio.
- **Desativar botões neste site** remove a preferência/permissão, os candidatos
  e os botões de todas as abas/frames conhecidos daquele site. Mensagens de
  documentos antigos deixam de autorizar novos envios. Uma procura manual pode
  readquirir acesso somente aos documentos atuais da aba escolhida.

Nenhum site recebe acesso automático na instalação. As permissões são
`activeTab` e `scripting`, mais acesso HTTP(S) opcional solicitado por site.
Não há permissão de cookies ou histórico. Os metadados de descoberta ficam
somente na sessão do navegador, com limite de 8 abas e 24 candidatos por aba;
listas incompletas são indicadas. Um botão em outro player ainda pode ser usado.

A identidade de cada frame/documento vem do navegador (Chrome/Edge 106+).
Navegação invalida os candidatos. Depois de uma navegação completa, a procura
manual precisa ser solicitada novamente; acesso temporário não vira autorização
automática por site. No modo automático, voltar a uma página pelo cache de
navegação restaura os botões somente após revalidar a permissão e o documento.
O descarregamento de um iframe limpa apenas os dados daquele documento; um
aviso atrasado não apaga os dados de um documento mais novo no mesmo frame.

Fontes diretas HTTP(S) de elementos `<video>`/`<audio>` são reconhecidas.
Players dentro de **shadow DOM aberto** também são examinados: no máximo 32
raízes, 4096 elementos examinados e 64 players por varredura. Raízes descobertas
recebem observação de alterações/eventos; uma procura limitada periódica também
detecta raízes abertas anexadas posteriormente. Uma lista parcial é indicada.

Quando um player interno usa `blob:`, o `src` público do componente estrutural
que o contém pode identificar a fonte: `hlsjs-video`/`hls-video` indicam HLS e
`dash-video` indica DASH. Esse adaptador percorre até oito hosts de shadow DOM;
não inspeciona estado interno do site nem decodifica parâmetros do proxy. O
aplicativo valida o manifest em vez de aceitar o nome do componente como prova.

Para outros players `blob:`, a extensão consulta **Resource Timing** do documento
autorizado e observa novas entradas, sem efetuar requisições, ler corpos,
interceptar cookies ou ampliar permissões. URLs `.m3u8`, `.mpd` e arquivos de
áudio/vídeo são candidatos; um MIME exposto pelo navegador pode identificar um
manifest sem extensão. São processadas até 512 entradas recentes por lote e
retidas até 24 fontes. Entradas antigas podem já ter sido descartadas pelo
navegador; iniciar novamente o vídeo pode produzir uma nova entrada.

Esses recursos pertencem ao **frame**, sem associação comprovada a um player.
O botão **Escolher fonte detectada** exige seleção explícita e mostra aviso
sobre anúncios/segmentos. Mesmo uma fonte única não é enviada automaticamente.
O popup mostra protocolo, host e caminho sem query para distinguir alternativas.
Manifests são enviados ao aplicativo, que deve oferecer formatos e informar
proteção, autenticação ou protocolos não suportados. Detecção não prova que um
download será possível nem identifica automaticamente o vídeo principal.
No YouTube, o botão envia o vídeo da página ao resolvedor do aplicativo,
mesmo quando o player usa `blob:`. Não são capturados segmentos de rede nem
sessões de login. Iframes de outra origem exigem sua própria permissão.
Manifests sem extensão e sem MIME visível continuam desconhecidos, a menos que
um componente suportado exponha sua fonte. DRM e acesso autenticado não são
resolvidos pela extensão. Players dentro de shadow DOM fechado e o modo nativo de tela cheia podem não
expor o botão; o popup é a alternativa quando a mídia foi identificada.

## Configuração manual no Windows

1. Mantenha o executável independente do OpenDownloader em uma pasta permanente. O build
   usado como host precisa ter stdin/stdout binários disponíveis e reconhecer
   o argumento `chrome-extension://ID/` como modo Native Messaging. Builds
   PyInstaller `--windowed` não oferecem esses streams do Python; use o host
   com console fornecido no pacote, quando houver.
2. Abra `chrome://extensions` ou `edge://extensions`, ative **Modo do
   desenvolvedor** e escolha **Carregar sem compactação**. Selecione esta pasta.
3. Confira o ID exibido pelo navegador (32 letras entre `a` e `p`). O manifesto inclui
   uma chave pública fixa: o ID esperado desta versão é `meicfjedeegbopmeifconaiekbmppbai`.
4. Execute manualmente no PowerShell, ajustando os caminhos e o ID:

   ```powershell
   .\tools\register_browser.ps1 -ExtensionId 'ID_DE_32_LETRAS' -AppExecutable '.\dist\LumeDownloads\LumeNativeHost.exe' -WhatIf
   .\tools\register_browser.ps1 -ExtensionId 'ID_DE_32_LETRAS' -AppExecutable '.\dist\LumeDownloads\LumeNativeHost.exe'
   ```

   Os comandos partem da pasta do aplicativo, um nível acima desta pasta. O
   primeiro mostra o plano. O segundo grava o manifesto e os registros HKCU
   de Chrome e Edge, sem instalar ou abrir programas. Não é necessário executar
   como administrador. Políticas corporativas podem bloquear hosts por usuário.
   O manifesto fica em `dist/BrowserHost` no projeto-fonte ou `BrowserHost` no
   pacote portátil, fora do AppData que pode ser virtualizado por aplicativos MSIX.
5. Ao atualizar esta extensão já carregada, use **Recarregar** na página de
   extensões de cada navegador. A chave pública permanece a mesma.
6. Escolha **Baixar com OpenDownloader** no clique direito de um link HTTP(S),
   ou cole um endereço no botão da extensão. Confirme as opções no aplicativo.

Chrome e Edge devem manter o mesmo ID para o manifesto com a chave pública fornecida.
Se o navegador exibir outro ID, confira a pasta/manifesto antes de registrar;
o script conserva as origens anteriores. O registro é idempotente, mantém recibo com hash e cria
backup antes de modificar configuração própria. Configuração existente sem
recibo ou com conteúdo alterado é recusada para preservar arquivos de terceiros.

O manifesto não aceita argumentos de linha de comando. Ele aponta diretamente
para o executável; o navegador fornece a origem e `--parent-window` ao iniciá-lo.

## Contrato local

- stdin/stdout: comprimento de 4 bytes little endian, seguido de JSON UTF-8.
- Pedido: `{"url":"https://example.org/arquivo.zip"}`; no máximo 64 KiB por
  mensagem e 8192 caracteres por URL; somente HTTP(S), sem credenciais embutidas.
- Mídia reconhecida: `{"version":2,"url":"https://example.org/video.mp4","title":"Título","media":true}`.
  Título tem no máximo 256 caracteres e é uma sugestão de exibição. Não escolhe
  caminho, executável, cabeçalhos, cookies ou formato de saída. A seleção pertence
  à confirmação no aplicativo; `media:true` representa áudio ou vídeo.
- Streaming: `{"version":3,"url":"https://example.org/manifest","title":"Título","media":true,"source":"hls"}`.
  `source` admite somente `hls` ou `dash`, preservando a interpretação de URL/MIME
  ou do componente público mesmo em URLs sem extensão. O aplicativo anterior que
  só aceita versão 2 precisa ser atualizado; o host deve recusar versões desconhecidas.
- Resposta: `{"ok":true,"id":"uuid"}` ou `{"ok":false,"error":"mensagem"}`.
- Publicação: `data_dir/inbox/<uuid>.json` contendo o pedido validado.
  Arquivos temporários começam com ponto; consumidores devem ignorá-los,
  reclamar os `.json` prontos por rename atômico e conservar claims em falhas.
- O host não baixa o recurso. Após persistir o pedido, solicita somente o companion
  fixo `LumeDownloads.exe --from-browser --data-dir ...`, sem passar URLs como argumentos.
  A GUI usa o bloqueio de instância do perfil; se já estiver aberta, a nova invocação sai.
  Falha ao abrir a GUI conserva o pedido e retorna aviso. `--native-host` mantém
  o modo diagnóstico de entrada sem solicitar abertura. O ambiente não é registrado.

## Verificações e limites

`python -m unittest discover -s tests -p test_browser.py -v` verifica o protocolo
e arquivos reais temporários a partir da pasta do aplicativo.
`node browser-extension/tests/native-handoff.test.cjs` verifica o worker,
permissões, invalidação por navegação, limites e revogação com API simulada.
`node browser-extension/tests/media-core.test.cjs` verifica a interpretação
compartilhada de URLs/mídias e o ID estável. Esses testes não comprovam o DOM,
a aparência dos botões ou o fluxo real do Chrome/Edge. Node não é requisito
para usar a extensão.

`node browser-extension/tests/context-menu.test.cjs` reproduz menu ausente no
startup, atualização sem remoção, recuperação pelo popup, erro de callback e
chamadas concorrentes, com API simulada. Referência:
[Chrome contextMenus](https://developer.chrome.com/docs/extensions/reference/api/contextMenus).

`node browser-extension/tests/content-lifecycle.test.cjs` também cobre o chooser
sem envio automático, Resource Timing limitado, limpeza por navegação, shadow DOM
aberto aninhado e o adaptador de componente com URL sem extensão. DOM e APIs são
simulados. A fixture `tests/streaming-player-fixture.html` pode ser servida via
HTTP com manifests locais para teste manual; é sintética e não comprova playback.

Referências de descoberta: [Resource Timing](https://www.w3.org/TR/resource-timing/),
[PerformanceObserver](https://developer.mozilla.org/en-US/docs/Web/API/PerformanceObserver/observe) e
[fontes de componentes Video.js](https://videojs.org/docs/framework/html/guides/media-sources).

O registro e a integração no navegador real precisam ser homologados pelo
usuário após a configuração. Testes em memória não comprovam políticas, IDs,
inicialização do executável pelo navegador ou funcionamento de uma instalação
específica.

Referências do protocolo: [Chrome Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)
e [Microsoft Edge Native Messaging](https://learn.microsoft.com/en-us/microsoft-edge/extensions-chromium/developer-guide/native-messaging).

## Preparação para as lojas

A extensão 0.4.0 inclui **Gravar reprodução** no popup. A aba de gravação pede a
fonte pelo seletor do navegador, oferece formatos disponíveis, resolução máxima,
pausa e encerramento. Compartilhe o áudio da aba se desejar som; não há pedido
de microfone nem novas permissões de extensão. O aplicativo solicita pasta e
nome ao terminar. Mantenha a aba aberta até receber o recibo. Conteúdo protegido
pode ter captura bloqueada. Veja [procedimento e limites](../docs/OPENDOWNLOADER-1.11.0.md).

Versão 0.3.4 preparada localmente, ainda não publicada. Os ícones originais são gerados por `tools/create_extension_icons.py`; `tools/package_extension.py` gera um ZIP mínimo e determinístico e seu inventário de hashes. As descrições, justificativas de permissões e pendências estão em `docs/STORE-LISTING.md`. A identidade local e o protocolo não mudaram; IDs atribuídos pelas lojas ainda precisam ser confirmados.
