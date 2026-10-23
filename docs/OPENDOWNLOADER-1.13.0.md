# OpenDownloader 1.13.0 — análise antecipada, faixas e recorte

O aplicativo mantém os nomes internos, o ID da extensão e os dados existentes.
A extensão passa a 0.6.0, sem permissões adicionais; recarregue-a no Chrome e Edge.

## Análise antes da transferência

Mídias diretas com Range e validador estável são examinadas por intervalos de até
64 KiB. A mesma interpretação MP4 da guarda local identifica `encv`/`enca`, inclusive
com `moov` no fim, saltando o payload `mdat`. A inspeção limita-se a 32 intervalos
e 2 MiB, além do teste inicial de um byte. A inicialização fMP4 não declarada como
criptografada também é inspecionada antes de disparar os segmentos em paralelo.

Servidor sem intervalos estáveis, formato desconhecido ou orçamento esgotado
significa **não classificado**, nunca garantia de ausência de proteção. A guarda
local anterior continua ativa na publicação. Um servidor que ignora Range pode
enviar bytes aos buffers de rede, mas o aplicativo não consome a resposta inteira
para essa inspeção. Arquivos explicitamente solicitados como bytes mantêm o contrato.

## Recuperação dos segmentos

Corpos incompletos, timeouts e falhas transitórias podem repetir um segmento até
três tentativas, com espera cancelável. A identidade remota é fixada antes da leitura.
Somente o `.part` não publicado e pertencente à tarefa é descartado; recibos e
segmentos completos não são sobrescritos. Identidade alterada, falta de validador,
TLS inválido, falha de permissão ou erro de formato não entram em repetição cega.
As retentativas de conexão/status continuam no transporte, separadas do orçamento
de corpo. Não há novas tentativas infinitas nem bypass de autenticação.

## Áudio e legendas

Em **Consultar formatos**, cada opção HLS/DASH identifica qualidade, áudio e legenda.
O usuário escolhe uma faixa de áudio e, opcionalmente, uma faixa WebVTT; a opção sem
legenda continua disponível. O ID escolhido é persistido e precisa continuar válido
na tentativa seguinte. Combinações são limitadas a 128, sem truncamento silencioso.

HLS aceita WebVTT em playlists VOD segmentadas; DASH aceita WebVTT em um arquivo
BaseURL. Legendas são incorporadas ao MP4 como `mov_text`, sem recodificar áudio ou
vídeo. O mapa HLS `X-TIMESTAMP-MAP` considera a origem temporal da mídia. Cues idênticos
entre segmentos são eliminados. Regiões/estilos avançados, mapas ambíguos/fora da
timeline, legendas DASH segmentadas/fMP4, TTML, legendas vazias e transmissões ao vivo
têm erro explícito. O texto é limitado a 16 MiB e 100.000 cues. Não há exportação SRT
nesta entrega. A formatação visual não é preservada integralmente pelo `mov_text`.

## Área de gravação

Na extensão, abra **Gravar reprodução → Escolher tela ou aba**. Depois do seletor
do navegador, ajuste a área arrastando na prévia ou informando esquerda, topo,
largura e altura em pixels. Clique **Gravar área selecionada** para começar; antes
disso não é criado um arquivo de gravação na ponte. **Superfície inteira** restaura
a captura direta. A seleção é ajustada antes do início; não muda durante a gravação.

O recorte usa canvas e mantém o áudio inteiro que foi compartilhado. Não usa microfone.
As dimensões de saída são pares e o recorte limita-se a 4096×4096 e 16 megapixels.
Para superfícies maiores, escolha qualidade de captura menor ou uma área compatível.
Mantenha a aba de gravação visível: suspensão/throttling do navegador pode reduzir
quadros. A prévia fica silenciosa e não prova que o áudio foi compartilhado.
O recorte não remove DRM e pode continuar preto ou sem áudio em conteúdo protegido.

## Criptografia autorizada e limites

Permanecem HLS AES-128/identity, SAMPLE-AES TS H.264/AAC e CENC/CBCS nos contêineres
suportados, com acesso autorizado às chaves. Os catálogos DASH sinalizam a exigência
de chaves. Chaves KID explícitas permanecem somente na sessão. Não há extração de
CDM, exploração de licenciamento ou promessa de exportar Netflix/HBO/qualquer serviço.

Fontes de formato: [RFC 8216](https://www.rfc-editor.org/rfc/rfc8216.html),
[W3C — Common Encryption MP4](https://www.w3.org/TR/eme-stream-mp4/),
[Canvas captureStream](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/captureStream).
