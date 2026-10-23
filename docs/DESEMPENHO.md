# Desempenho — Lume 1.6

## Ajustes da 1.6

O buffer dos segmentos HLS/DASH passou de 64 para 256 KiB. A reserva de disco
inclui os buffers simultâneos: com oito workers, passa de 0,5 para 2 MiB, além
da margem e do intervalo de checagem existentes. Os tetos de conexões, arquivos
abertos e fila permanecem iguais. Downloads HTTP diretos já usam blocos de 1 MiB.

Foram concluídas 18 transferências locais de 256 MiB em 128 recursos, oito conexões
e hashes corretos: 12 rodadas HTTP/HTTPS de comparação e seis rodadas HTTPS extras.
Nos cinco pares HTTPS de 64/256 KiB combinados, wall médio 2,771/2,718 s e CPU
2,859/2,634 s. Isso representa aproximadamente 1,9% no tempo e 7,9% em CPU nesse
fixture; não uma promessa de ganho em qualquer site. Cliente, servidor TLS e
coletor estavam no mesmo processo, com cache inicializado. Na repetição adicional,
o pico RSS amostrado foi 41,3–42,2 MB e 42,9–43,8 MB respectivamente, sem filhos.
Comparar memória entre modos é limitado pela retenção do alocador no processo.

1 MiB foi descartado para streaming: em corpo lento o cancelamento levou 2,507 s,
contra 0,221/0,204 s com 64/256 KiB. A regressão adicional mantém o restante do corpo
bloqueado e exige interrupção antes de sua liberação. Pausa, retomada, TLS, espaço
livre e cleanup continuam sendo verificados por testes reais.

O dono do remux agora fornece `remux_prepare_s`, `remux_child_s`, `remux_fsync_s`
e `remux_probe_s`. São subfases contidas no agregado `remux_s`: não devem ser
somadas novamente ao total. Metadados ffprobe continuam separados dos tempos.
Não foram retirados fsync, SHA-256 ou verificações de mídia para obter ganho.

HTTP/2 continua fora do runtime. Um protótipo TLS multiplexado do backend avaliado
preservou a outra transferência ao cancelar uma stream, mas cancelamentos repetidos
esgotaram o limite remoto por ausência de RST_STREAM. O teste não justifica integrar
esse backend sem resolver ownership e liberação de streams. HTTP/3 também não foi
adicionado. Novas filas nomeadas compartilham o mesmo transporte e teto global.

## Mecanismo de transferência

A opção **Conexões por arquivo** controla de 1 a 8 workers. O Manager compartilha
até 8 conexões por servidor entre as tarefas; escolher 8 em duas tarefas não
produz 16 conexões ao mesmo servidor. O padrão permanece 4. A preferência de
limite global continua valendo; zero significa sem limite definido pelo Lume.

Arquivos com tamanho, intervalos e identidade verificáveis são divididos em
blocos de trabalho. Cada conexão livre assume o maior bloco pendente. O alvo é
8 MiB por bloco, com pelo menos dois blocos por worker quando há tamanho
suficiente e até 128 entradas para limitar o custo das gravações da fila.
Arquivos maiores têm blocos maiores. Mapas de downloads antigos são preservados
na retomada. O método distribui trabalho entre workers; não reparte intervalos
que já estão sendo recebidos. Sem intervalos seguros, usa conexão única.

HLS/DASH baixa recursos independentes em paralelo e monta as faixas na ordem do
manifest. Intervalos da mesma URL/cabeçalhos permanecem serializados para
preservar identidade/If-Range. Cancelamento ou falha aguarda todos os workers;
recibos válidos permitem retomada. Não há execução de mídia nem recodificação.

O transporte reutiliza conexões HTTP/1.1 por origem (esquema, host e porta),
somente após consumir e validar toda a resposta. Mantém até 32 ociosas, com
validade de 15 s conferida durante atividade; o fechamento do Manager encerra
o pool. Sockets fechados, respostas incompletas, exceções e cancelamento são
descartados. Até três tentativas de abertura GET/HEAD recuperam falhas transitórias, HTTP 408/429/5xx
selecionados, com espera cancelável e Retry-After limitado. Após entregar o corpo ao
consumidor não há repetição automática; a identidade e a retomada continuam sob o Manager.
TLS continua verificando certificado e hostname. HTTP/2 e HTTP/3 não são usados.

## Medições reproduzíveis

Os scripts em `tools/benchmark_*` concluem as transferências e conferem hashes;
os recibos ficam em `.cache/benchmark-*.json`. São experiências locais com
latência/assimetria controladas, não estimativas do ganho em qualquer site.
Há comparação real com o IDM 6.43 em 2160p e arquivos controlados, registrada no
relatório local da versão. Não houve execução com cache frio do sistema.

- `benchmark_balanced_downloads.py`: 32 MiB, quatro workers, primeiro intervalo
  mais lento. Reproduz a divisão estática da 1.3 sobre o runtime atual e compara
  com blocos; keep-alive desativado nos dois lados. Não executa um binário antigo.
  Mede CPU de todo o processo (incluindo servidor), RSS amostrada a cada 5 ms
  e tempo da tarefa completa; não tem subprocessos.
  Em três execuções: divisão estática 0,9652/0,9428/0,9398 s (média 0,9493 s);
  blocos 0,8358/0,6797/0,6779 s (média 0,7311 s), redução de cerca de 23%.
  CPU: 0,2656–0,3750 s versus 0,2812–0,3281 s. Pico RSS amostrado:
  69.279.744–71.028.736 B versus 70.078.464–71.385.088 B. Inclui limpeza final
  completa; essa rodada substitui medições preliminares anteriores à correção.
- `benchmark_parallel_streaming.py`: vídeo gerado de três segundos, HTTP/1.0
  com 120 ms por segmento, serial versus quatro workers. HLS: 0,6967–0,7173 s
  versus 0,4459–0,4557 s. DASH: 1,6067–1,6380 s versus 0,6981–0,7542 s.
  CPU da família: 0,1250–0,2500 s; pico RSS amostrado: 55.824.384–58.126.336 B.
  Inclui FFmpeg/ffprobe com handles de processo retidos; amostragem de 5 ms
  pode perder picos curtos. Duas passagens no processo já inicializado.
- `benchmark_connection_pool.py`: 4 MiB em 16 respostas, custo controlado de
  35 ms por nova conexão. Sem reutilização: 0,6034/0,5976 s (primeira/segunda).
  Com reutilização: 0,0535/0,0163 s; uma conexão em vez de 16. CPU: 0,03125/0,06250 s
  versus 0,015625/0,015625 s. Pico RSS amostrado a cada 2 ms: 34.349.056/34.357.248 B
  versus 34.983.936/34.996.224 B. A primeira passagem tem pool novo; a segunda
  reutiliza o pool. Não mede ganho de handshake TLS; inclui servidor e cliente
  no mesmo processo e não tem filhos.

Tokens do agente, GUI/navegadores, fontes remotas, banda limitada, disco de outro
computador e cache frio do sistema não foram medidos. Os ganhos não devem ser
somados nem extrapolados para a internet. Mais conexões podem não ajudar quando
o servidor ou uma única conexão já entrega toda a banda disponível.

## I/O e montagem na versão 1.5

Arquivos diretos usam leituras de 1 MiB e checkpoints a cada 8 MiB. Cada checkpoint
continua sincronizando dados antes de persistir o mapa de retomada. Mapas existentes
são preservados. Isso reduz chamadas de disco e gravações de SQLite.

Streaming mantém os segmentos e recibos como cache verificável, sem sincronizar
cada arquivo no caminho de sucesso. Pausa ou falha aguarda os escritores e sincroniza
o cache. Uma interrupção abrupta de processo/energia pode perder cache ainda não
confirmado: a retomada verifica SHA-256 e identidade remota e recusa cache danificado.
O arquivo final continua sincronizado, verificado e publicado sem sobrescrita.

Até 768 arquivos de segmentos são lidos diretamente pelo protocolo local concatf do
FFmpeg. A lista contém apenas caminhos próprios, com parser e whitelist file/concatf;
nenhum manifest remoto é entregue ao FFmpeg. O limite contém descritores e buffers.
Planos maiores usam a montagem intermediária anterior. As consultas de espaço livre são compartilhadas
por até 8 MiB/0,5 segundo, com reserva para os escritores ativos.

A fila persiste tempos de resolução, transferência, agregação, remux, verificação,
publicação e limpeza. Transferência de streaming inclui validação dos segmentos e
espera local; não é uma medição independente de bytes na rede.

O remux de streaming grava MP4 local padrão com `moov` no fim, evitando o
reposicionamento `faststart` que reescreve o arquivo. O Lume disponibiliza o arquivo
somente depois de completo; não oferece reprodução progressiva desse temporário.
Não há recodificação nem alteração da qualidade escolhida. FFprobe e SHA-256
continuam sendo exigidos antes da publicação. O fluxo YouTube separado conserva
sua montagem existente.
