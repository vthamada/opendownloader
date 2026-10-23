# OpenDownloader 1.9.0 — HLS AES-128 autorizado

O aplicativo aceita VOD HLS com `METHOD=AES-128` e `KEYFORMAT=identity`
(ou formato omitido), quando o servidor fornece acesso autorizado à chave.
O fluxo continua exigindo confirmação antes do download. Consultar formatos
ou detectar um player não busca chaves nem segmentos.

## Suporte entregue

- Chave binária de exatamente 16 bytes, obtida pelo transporte HTTP(S) existente.
- AES-128 CBC e PKCS#7 por segmento; IV explícito ou sequência HLS de 64 bits.
- Rotação com URLs de chave distintas, passagem para METHOD=NONE e áudio separado.
- MPEG-TS e MP4 fragmentado; inicialização criptografada exige IV explícito.
- Byte ranges de segmentos completos; playlists especiais de I-frames são recusadas.
- EXT-X-SESSION-KEY validado no catálogo, sem busca antecipada da chave;
  cada playlist de mídia continua declarando as chaves aplicáveis aos segmentos.
- Transferências paralelas, cache criptografado e retomada com verificação da
  identidade remota, do hash local e da impressão SHA-256 da chave recém-obtida.

O mesmo endereço de chave que retornar valores diferentes em declarações distintas
é recusado para evitar ambiguidade. Cada declaração possui identidade própria.
Chaves são coalescidas por declaração/cabeçalhos durante um download; uma nova
execução as busca novamente. Não há cache global ou persistência da chave bruta.

## Recursos e propriedade

Chaves permanecem somente em memória; referências são liberadas após concluir os
workers, sem alegação de apagamento seguro da memória do Python/backend crypto.
Não são passadas para FFmpeg, shell ou linha de comando. Uma impressão SHA-256
da chave liga os recibos criptografados à retomada; não contém a chave bruta.

O cache guarda ciphertext. Entradas locais descriptografadas possuem nomes e
proprietário explícitos, são processadas em blocos e removidas após o remux ou
cancelamento. Se um subprocesso ainda possui essas entradas, a propriedade é
retida até sua limpeza. A fila e a publicação final continuam pertencendo ao Manager.

A chave usa as mesmas verificações TLS, recusa de downgrade, cancelamento e
restrições de cabeçalhos do transporte. Não há importação de cookies, captura de
sessões ou tentativa de contornar HTTP 401/403. CBC não autentica conteúdo:
padding válido e SHA-256 calculado localmente não substituem hash esperado de
fonte confiável. Os fixtures são comparados por decode completo de áudio e vídeo.

## Matriz de criptografia

| Modalidade | Situação |
|---|---|
| HLS AES-128/identity, CBC + PKCS#7 | Implementado; evidências da rodada no relatório ao lado do instalador |
| HLS SAMPLE-AES em MPEG-TS | Ainda não implementado; exige parsing de amostras/NALs, padrões de blocos e IVs próprios |
| HLS SAMPLE-AES em fMP4/CMAF (`cbcs`) | Ainda não implementado; exige metadados de criptografia de amostra e mapa KID/chave autorizado |
| SAMPLE-AES-CTR | Ainda não implementado; CTR não usa a rotina CBC e tem restrições de interoperabilidade |
| DASH CENC: `cenc`, `cens`, `cbc1`, `cbcs` | Ainda não integrado; as opções anunciadas pelo FFmpeg privado não comprovam suporte do aplicativo |
| ClearKey | Ainda não integrado; é um formato JWK/KID de entrega de chaves, não outro algoritmo |
| Widevine, PlayReady e FairPlay | Dependem de integração autorizada com o serviço/CDM/licenças; não fornecidos por esta atualização |

Não existe promessa de “todos os sites” ou “todas as criptografias”. Metadados
incompatíveis são recusados com mensagem específica antes da busca da chave.
Esta versão não demonstra download reproduzível de episódios da HBO/Netflix.

Fontes primárias:

- [RFC 8216 — HLS](https://www.rfc-editor.org/rfc/rfc8216)
- [Apple — Sample Encryption](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/HLS_Sample_Encryption/Encryption/Encryption.html)
- [Apple — CMAF/HLS](https://developer.apple.com/documentation/http-live-streaming/about-the-common-media-application-format-with-http-live-streaming-hls)
- [FFmpeg — MOV demuxer](https://www.ffmpeg.org/doxygen/trunk/mov_8c_source.html)
- [W3C — Clear Key](https://www.w3.org/TR/encrypted-media/#clear-key)
- [Widevine](https://developers.google.com/widevine/drm/overview), [PlayReady](https://learn.microsoft.com/en-us/playready/overview/license-and-policies), [FairPlay](https://developer.apple.com/streaming/fps/)

## Aplicação e navegadores

O instalador 1.9.0 preserva a fila, os downloads e backup da instalação anterior.
A extensão é 0.3.5 e mantém seu ID/permissões. Carregue a pasta indicada pelo
assistente e confira a versão no popup. A correção de frames about:blank está
incluída. O relatório da rodada distingue testes simulados de prova real no Chrome
e Edge; gerar/instalar o pacote não confirma sozinho esse fluxo.
