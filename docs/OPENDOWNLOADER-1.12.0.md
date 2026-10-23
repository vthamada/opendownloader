# OpenDownloader 1.12.0 — faixas de áudio e controle da gravação

Entrega local para Windows, com extensão 0.5.0. Os nomes internos, o host
`app.lume.downloads`, o ID local da extensão e o perfil existente são preservados.
Atualizar os arquivos exige recarregar a extensão no Chrome e no Edge.

## Downloads

O catálogo de manifests HLS oferece as faixas de áudio externas do grupo de cada
qualidade. Consulte os formatos e escolha o idioma/nome da faixa desejada. A
escolha padrão anterior continua válida. A mesma seleção explícita precisa estar
disponível para retomar; uma faixa removida não é substituída silenciosamente.
Variantes com legendas externas indicam que a saída é sem legendas. Não há
conversão de codecs, download de legendas nem suporte a live nesta entrega.

O paralelismo existente é mantido: até oito conexões por arquivo/servidor, limites
compartilhados pela fila, reutilização de conexões e verificação da identidade de
intervalos. Esta versão não promete ganho de velocidade de rede.

## Gravação

Na extensão, escolha **Gravar reprodução**. Antes de gravar, selecione formato,
resolução, bitrate solicitado e duração máxima opcional. O navegador ainda exige
seleção explícita da superfície e compartilhamento do áudio. Não há microfone.

- A prévia local permanece silenciosa para evitar eco e é liberada ao encerrar.
- As opções de bitrate são automática, 2,5, 6 e 12 Mb/s de vídeo. O navegador
  decide o valor efetivo; se rejeitar a opção, a interface informa o fallback.
- A duração conta apenas o tempo ativo: pausar exclui esse intervalo. Atingir o
  limite usa o mesmo encerramento que **Parar e salvar**, incluindo último bloco,
  confirmações de recebimento e recibo. Suspender a aba pode atrasar o timer.
- Perda/ausência de áudio recebe aviso. Ter uma faixa de áudio não comprova que
  há som útil. A prévia também não prova a qualidade do arquivo final.
- O aplicativo continua exigindo confirmação de pasta/nome, verifica os dados e
  preserva o formato MP4/WebM; não recodifica nem sobrescreve arquivos existentes.

## Diagnóstico de proteção

Na busca autorizada, a extensão observa somente a presença de `mediaKeys` no
player ou o evento nativo `encrypted`. O aviso significa **API EME observada**.
Não identifica Widevine/PlayReady, a licença, as chaves, nem se uma URL detectada
é o filme. Sem esse sinal, a proteção permanece desconhecida. Recursos observados
no frame não herdam o sinal de um player: podem ser anúncios ou outros vídeos.

Nenhuma sessão, `initData`, chave, mensagem de licença ou memória do CDM é lida.
A gravação usa as APIs normais do navegador e pode ser bloqueada, preta ou muda.
Downloads com chaves autorizadas continuam nos limites da versão 1.10.0.

## Frentes que exigem insumos externos ou outro ciclo

| Frente | Estado |
| --- | --- |
| Login/conectores autenticados | Sem conector implementado; exige definir provedor, API, escopos e autorização. Não importa cookies do navegador. |
| Player DRM e offline oficial | Sem integração licenciada. Depende de SDK/acordo, servidor de licenças e políticas do provedor. Não produz MP4 irrestrito por definição. |
| Live, legendas e múltiplos períodos DASH | Ainda não implementados; exigem semântica própria de janela, descontinuidade, faixas e retomada. |
| Recuperação de gravação interrompida/divisão em volumes | Originais/parciais são preservados; não são marcados como completos nem reparados automaticamente. |
| Extração de chaves do CDM/falhas de licenciamento de terceiros | Não implementadas. |

## Validação

Testes de contrato simulam os controles de captura e exercitam a observação EME.
Testes Python usam manifests e mídia sintética local. O pacote é validado com
executáveis reais, host nativo, fila, FFmpeg/ffprobe privados e hashes. Esses
testes não comprovam os controles reais do Chrome e Edge. A prova assistida deve
registrar cada navegador, o arquivo final, hash e decodificação de áudio/vídeo;
o relatório externo do instalador informa o estado observado de cada etapa.

Referências primárias: [HLS RFC 8216](https://datatracker.ietf.org/doc/html/rfc8216),
[EME](https://www.w3.org/TR/encrypted-media/) e
[captura no Chrome](https://developer.chrome.com/docs/extensions/how-to/web-platform/screen-capture).
