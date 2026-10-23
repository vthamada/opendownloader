# OpenDownloader 1.11.0 — gravação da reprodução

A extensão 0.4.0 oferece **Gravar reprodução** no popup. Ela abre uma aba própria,
com seleção explícita da aba, janela ou tela pelo navegador. O aplicativo recebe
o arquivo localmente e oferece confirmação de nome, pasta e fila ao terminar.

## Usar

1. Atualize o aplicativo e recarregue a extensão no Chrome e no Edge. Confira
   **0.4.0** no popup e **1.11.0** na verificação de conexão com o aplicativo.
2. Abra o vídeo e, no popup, escolha **Gravar reprodução**. Escolha nome, formato
   e resolução máxima. Somente formatos aceitos pelo MediaRecorder aparecem.
3. Clique em **Escolher e gravar**. No seletor do navegador, escolha a aba do
   vídeo e compartilhe seu áudio. Janela/tela podem não oferecer áudio. O
   microfone não é solicitado. Sem áudio, é necessário confirmar a captura muda.
4. Reproduza o vídeo. Use **Pausar**, **Continuar** e **Parar e salvar** na aba de
   gravação. Mantenha essa aba aberta até receber o recibo de recebimento.
5. No aplicativo, escolha a pasta e confirme **Salvar agora** ou adicione à fila.
   O aplicativo confere SHA-256, contêiner e pacotes de vídeo/áudio antes de
   publicar o arquivo. Nomes existentes recebem uma nova cópia por padrão.

A gravação acontece no tempo da reprodução. Qualidade, taxa de quadros e áudio
dependem da superfície compartilhada, navegador, codificação e computador. Ela
não conserva necessariamente os bytes, resolução ou qualidade do arquivo fonte.
MP4 e WebM são mantidos no formato gravado; esta versão não os converte.

## Limites e recuperação

- Capturas limitadas a 64 GiB e 24 horas de sessão. Há verificação de espaço e
  limite de 1000 entradas na pasta. O navegador permite no máximo 32 MiB de
  dados pendentes de confirmação; seus próprios buffers de captura são extras.
- Blocos de até 36 KiB usam Native Messaging persistente, sequência estrita,
  confirmação e SHA-256. A extensão nunca escolhe caminhos de arquivo. O recibo
  pronto só é publicado após sincronização em disco.
- Fechar a aba, perder conexão ou ficar sem espaço interrompe a captura. Os
  dados recebidos ficam como parcial e podem não ser reproduzíveis. Encerrar o
  processo à força pode deixar recibo `recording`; ele nunca é importado como
  arquivo completo.
- Originais e parciais ficam em `recordings` na pasta de dados. Cancelar/remover
  o pedido da fila preserva o original. Após conferir a cópia final, a pessoa
  pode arquivar/remover a pasta da captura. Não há limpeza automática nem
  retomada da gravação interrompida nesta versão.
- Salvar usa armazenamento adicional para parte e montagem final. Retomar essa
  cópia recomeça a leitura e o hash do original local, sem nova captura ou rede.
  A publicação nunca sobrescreve arquivos.

## Conteúdo protegido e alternativas autorizadas

A gravação usa `getDisplayMedia` e `MediaRecorder`, respeitando as proteções da
plataforma. Um player pode impedir a captura ou produzir imagem preta/sem áudio.
Não se promete gravação universal nem download de conteúdo de Netflix/HBO Max.
O recibo comprova bytes recebidos; a análise de pacotes não detecta se a cena
esperada foi gravada ou se uma imagem preta é intencional.

As alternativas são um arquivo/manifest disponibilizado para download pelo
responsável, chaves fornecidas explicitamente para os fluxos autorizados já
documentados, ou a função offline oficial da plataforma. Integração licenciada
com DRM/CDM e licenças offline depende de autorização e SDK do provedor; não
equivale a exportar um MP4 livre. Não foram implementadas extração de chaves do
navegador, alteração de CDM ou desativação de proteções.

Referências primárias: [Chrome](https://developer.chrome.com/docs/extensions/how-to/web-platform/screen-capture),
[Screen Capture](https://www.w3.org/TR/screen-capture/),
[MediaStream Recording](https://www.w3.org/TR/mediastream-recording/),
[Encrypted Media Extensions](https://www.w3.org/TR/encrypted-media/) e
[Widevine](https://developers.google.com/widevine/drm/overview).

## Validação

Os testes separam protocolo nativo real, fila e mídia local de simulações de
MediaRecorder. Seleção de fonte e áudio precisam ser homologados nos navegadores
reais. O relatório do instalador em `dist` registra os testes executados e as
etapas que ainda dependem dessa prova.
