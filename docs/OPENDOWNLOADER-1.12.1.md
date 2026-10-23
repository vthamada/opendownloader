# OpenDownloader 1.12.1 — sinalização de MP4 protegido

O botão de download do player exige um resultado de mídia. Antes de publicar o
arquivo, o aplicativo agora examina a hierarquia de descrições de amostras MP4.
Se encontrar `encv`/`enca`, a tarefa informa proteção/criptografia e não publica
o resultado como vídeo pronto. As partes ficam preservadas. A recuperação de uma
publicação antiga sem recibo final também aplica a mesma verificação, preservando
o arquivo já existente.

A inspeção é local, com cabeçalhos pequenos, limite de caixas/descrições,
cancelamento e conferência da identidade do arquivo. Ela pula os payloads `mdat`,
sem decodificar o filme, ler chaves/licenças ou adicionar requisições de rede.
O aviso ocorre na finalização; esta correção não evita a transferência anterior
dos bytes e não promete detecção antecipada de qualquer DRM ou formato.

Links tratados explicitamente como arquivos continuam permitindo a transferência
dos bytes originais. Formatos desconhecidos permanecem não classificados; ausência
de um marcador detectado não comprova reprodução nem ausência de DRM. Saídas de
streaming decriptadas com chaves autorizadas continuam passando pelo fluxo normal.

A extensão permanece 0.5.0. Não é necessário recarregá-la por causa desta correção
do aplicativo. O conteúdo já baixado e a fila existente não são convertidos nem
reescritos. Não há extração de chaves do CDM ou quebra do licenciamento comercial.

Caso analisado: `v2.mp4` informado pelo usuário tinha 654.822.842 bytes, somente
vídeo H.264 1024×576, descrição `encv` e esquema `cenc`, com metadados de proteção
em 900 fragmentos. A tentativa de decodificação sem decriptação falhou. O arquivo
original foi preservado. A referência de formato é
[W3C — ISO Common Encryption MP4](https://www.w3.org/TR/eme-stream-mp4/).

Os testes usam descrições sintéticas e o fluxo público navegador→fila, com
publicação recusada para mídia protegida, preservação de partes e recuperação.
A prova não implica download/desproteção de catálogos comerciais.
