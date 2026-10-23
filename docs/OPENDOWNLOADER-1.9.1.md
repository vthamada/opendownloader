# OpenDownloader 1.9.1 — atualização e HLS AES-128

Inclui o [suporte AES-128 autorizado da versão 1.9.0](OPENDOWNLOADER-1.9.0.md)
e a extensão 0.3.5 com correção da busca em páginas com frames mistos.

## Correção da atualização

Nesta máquina, todos os arquivos e o recibo da instalação 1.8.0 coincidiram com
o inventário de propriedade. A entrada de desinstalação coincidia em todos os
campos exceto DisplayVersion: ainda exibia 1.7.2. O plano antigo recusava a
operação por igualdade integral do registro, confundindo a versão exibida com
a identidade da instalação.

O novo plano aceita somente atraso de DisplayVersion: versões tipadas e
válidas, observada mais antiga que a registrada, recibo coerente e todos os
demais campos exatamente iguais. Caminhos, comando de desinstalação, nomes,
tipos/valores estranhos e versões futuras divergentes continuam recusados.
O plano expõe o reparo e mantém o snapshot observado para rollback; não muda
o recibo antigo antes da aplicação nem apaga registros desconhecidos.

As caixas de erro do NSIS usam `/SD IDOK`. A instalação silenciosa retorna
falha sem aguardar confirmação invisível; no fluxo interativo o erro continua
visível. Não foram alteradas proteções do Windows ou dos navegadores.

Fonte do comportamento silencioso:
[NSIS MessageBox](https://nsis.sourceforge.io/Reference/MessageBox).

## Escopo da validação

O relatório ao lado do instalador registra a rodada final: testes, verificação
do executável/payload, resultado da aplicação, backup e provas por navegador.
As evidências da versão 1.9.0 são históricas, não uma prova de instalação 1.9.1.
Nenhuma variante de SAMPLE-AES/CENC/DRM foi acrescentada neste patch.
