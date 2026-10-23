# Streaming no OpenDownloader 1.12

A extensão 0.5 descobre fontes; o aplicativo 1.12 interpreta manifests,
transfere segmentos por seu transporte HTTP/TLS e publica o arquivo somente
depois da verificação. FFmpeg lê apenas arquivos locais, sem acesso à rede.
O processo preserva as faixas, sem recodificação ou aumento de resolução.

Segmentos de recursos independentes são transferidos com 1 a 8 workers,
conforme **Conexões por arquivo**. Intervalos da mesma URL/cabeçalhos têm um
dono serial para preservar identidade e If-Range. A montagem segue a ordem do
manifest, mesmo quando os downloads acabam fora de ordem. Falha/cancelamento
interrompe e aguarda todos os workers antes de permitir limpeza ou nova tentativa.
O transporte compartilha até 8 conexões por servidor e reutiliza somente respostas
totalmente consumidas e válidas. Consulte [DESEMPENHO](DESEMPENHO.md).

| Fonte | Implementação | Limites |
| --- | --- | --- |
| Arquivo direto HTTP/HTTPS | Motor de arquivos existente | Fonte de mídia deve responder com MIME de áudio/vídeo no envio pelo player |
| YouTube público | Catálogo, seleção e HTTP de faixas | Sem login, playlists ou lives |
| HLS VOD | Master/media, TS, fMP4/MAP, intervalos e escolha de áudio externo; AES-128/identity e SAMPLE-AES TS H.264/AAC autorizados | ENDLIST obrigatório; sem live, legendas ou descontinuidades; SAMPLE-AES fMP4 não suportado |
| DASH VOD | MPD estático, um período, SegmentTemplate/Timeline e SegmentList; CENC/CBCS com KIDs/chaves autorizadas nos limites documentados | Sem licenciamento comercial, rotação de KID dentro da faixa, SegmentBase/SIDX ou vários períodos |
| Vimeo/Archive | Extratores públicos, catálogo e seleção | Catálogo não comprova que uma variante é transferível; criptografia/autenticação podem impedir |
| Dailymotion | Roteamento e erro de dependência | Componente de compatibilidade ausente nesta instalação |

Os contêineres de saída do streaming são MP4/M4A. A compatibilidade dos codecs
depende do FFmpeg existente e do contêiner de destino. Arquivos diretos mantêm
o formato original; não há conversão automática para MP3 ou outro codec.

## Descoberta no navegador

A busca percorre até 4.096 nós e 32 raízes de Shadow DOM aberto, retendo até
64 players. Fontes declaradas em `hlsjs-video`, `hls-video` e `dash-video` são
associadas estruturalmente ao player. URLs de manifests reconhecidas no Resource
Timing são candidatos do documento, sem atribuição automática a um player ou
suposição de que não sejam anúncios. O usuário escolhe o candidato desejado.

Os dados são temporários, vinculados a aba/frame/documento e invalidados na
navegação/revogação. A extensão mantém as permissões anteriores: acesso manual
ou automático por site autorizado, sem cookies, histórico ou interceptação de
requisições. Não extrai chaves, sessões ou estado interno do aplicativo da página.

Para fontes HLS/DASH reconhecidas, o protocolo nativo v3 preserva `source` como
indicação validada (`hls`/`dash`). O aplicativo confirma o protocolo pelo corpo
do manifest; um dado enviado pela página não autoriza comandos, caminhos ou
cabeçalhos. Mensagens antigas continuam compatíveis.

## Posse e recuperação

O parser limita manifest a 2 MiB e até 10.000 segmentos por tarefa. Cada segmento
tem limite de 512 MiB; a transferência total tem limite de 64 GiB. URLs de
segmentos aceitam somente HTTP/HTTPS, sem credenciais embutidas nem downgrade
HTTPS→HTTP. O XML recusa entidades/DOCTYPE e expansões excessivas.

Cada segmento recebe recibo com tamanho, hash e identidade do recurso após
sincronização em disco. Retomada exige identidade verificável e conferência do
servidor; intervalos do mesmo recurso preservam a identidade com If-Range.
Mudanças exigem reinício e não combinam versões de segmentos.

O módulo de streaming é dono dos nomes `streaming-*`, com marcador próprio.
A fila é dona da pasta da tarefa e da publicação final. Falhas de processamento
ou limpeza preservam a posse; o marcador é removido por último para permitir
nova tentativa. O destino existente nunca é sobrescrito.

## Evidência

O caso indicado pelo usuário expõe `VIDEO-MINIMAL-SKIN` → `HLSJS-VIDEO` →
`VIDEO(blob)`. A fonte pública declarada foi interpretada como HLS, com 300
segmentos de vídeo e 300 de áudio. Isso comprova leitura de metadados; não é
comprovação do download completo ou do botão real após atualizar a extensão.

Os testes manuais históricos da versão 1.3/extensão 0.3.0 em Chrome e Edge concluíram o fluxo
do segundo player da fixture com Shadow DOM aberto. Os registros do servidor,
SHA-256 e ffprobe dos arquivos finais foram conferidos. O Edge funcionou após
recarregar a extensão. Isso comprova a fixture local, sem ampliar a afirmação
para o download completo no site indicado. A nova versão tem regressões com
HTTP real e testes de DOM/APIs simulados; a homologação atual e suas limitações
ficam em `VALIDACAO.md`.

Referências: [HLS RFC 8216](https://www.rfc-editor.org/info/rfc8216/),
[DASH-IF timing model](https://dashif.org/Guidelines-TimingModel/),
[Resource Timing](https://developer.mozilla.org/en-US/docs/Web/API/PerformanceResourceTiming).
