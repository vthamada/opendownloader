# Atualizações locais assinadas

O Lume autentica um manifesto e um ZIP já salvos pelo usuário e os prepara em uma pasta nova. **Não baixa, aplica, instala ou executa a atualização.** O aplicativo em uso não é sobrescrito.

## Contrato para a interface

    from lume.updates import UpdateError, verify_update, stage_update

    verified = verify_update(
        manifest_path,
        package_path,
        trusted_public_key_b64,
        current_version="1.0.0",
    )
    prepared = stage_update(verified, staging_parent)

VerifiedUpdate contém version, filename, sha256, size e package_path. prepared é um Path para uma pasta nova:

- payload/: conteúdo extraído para revisão/aplicação manual depois de encerrar o aplicativo;
- release.zip: cópia dos bytes autenticados;
- manifest.json: manifesto utilizado na verificação.

UpdateError informa uma razão legível. Assinatura inválida, biblioteca criptográfica indisponível ou chave pública ausente bloqueiam a operação. Nenhuma chave vem embutida como se fosse uma identidade de produção.

A interface deve obter a chave pública confiável por canal independente e armazená-la na configuração do usuário. Nunca deve aceitar automaticamente uma chave que veio junto do pacote/manifesto. Quem puder alterar a configuração de confiança ou substituir o aplicativo local poderá alterar essa política.

## Formato

O envelope possui exatamente payload e signature. O payload possui exatamente os cinco campos abaixo:

    {
      "payload": {
        "app": "lume-downloads",
        "version": "1.0.1",
        "sha256": "<64 dígitos hexadecimais minúsculos>",
        "size": 12345,
        "filename": "Lume-1.0.1.zip"
      },
      "signature": "<assinatura Ed25519 de 64 bytes em base64>"
    }

Os bytes assinados são:

    json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

A chave pública é Ed25519 raw, 32 bytes em base64, fornecida separadamente. O manifesto é UTF-8 e tem limite de 1 MiB. Campos extras, chaves JSON duplicadas e constantes não JSON como NaN são rejeitados.

A versão deve ter três componentes numéricos sem zeros iniciais e ser estritamente maior que current_version. Isso bloqueia repetição/regressão em relação à versão que o chamador informa; não constitui proteção contra alguém com acesso local que altere o aplicativo ou essa referência. Préreleases não são aceitas nesta versão.

## Proteção do pacote e staging

- Pacote assinado com tamanho entre 1 byte e 1 GiB; nome deve coincidir exatamente com filename.
- SHA-256 e tamanho são conferidos por streaming.
- stage_update reautentica os metadados guardados e verifica novamente o conteúdo. Copia os bytes para um ZIP privado, confere a cópia e a extrai pelo mesmo handle aberto.
- O arquivo público original pode mudar entre verify_update e stage_update: a operação é bloqueada se os bytes copiados não corresponderem ao manifesto. A primeira verificação não é usada como autorização permanente para qualquer arquivo naquele caminho.
- Somente ZIP de volume único, sem ZIP64, com compressão Stored/Deflate.
- Até 4.096 entradas, 1 GiB expandido e diretório central de até 16 MiB. O tamanho do diretório e a contagem são limitados antes de ZipFile carregar seu índice.
- Caminhos absolutos, "..", barras invertidas, links, reparse points e tipos especiais são rejeitados.
- Nomes reservados do Windows, caracteres proibidos, ponto/espaço final e colisões por casefold/NFC são rejeitados; o mesmo vale para colisões arquivo/diretório.
- Os limites de entradas e expansão podem ser reduzidos pelos argumentos max_entries/max_expanded_bytes; não podem aumentar os tetos.
- A extração cria arquivos exclusivos e não restaura permissões executáveis do ZIP.

O diretório pai de staging deve ser controlado pela conta do usuário, sem outros usuários não confiáveis escrevendo nele. O módulo não promete isolamento contra processos maliciosos com o mesmo nível de acesso ao disco. Em POSIX a pasta temporária usa permissões privadas; no Windows devem ser usadas ACLs adequadas da conta.

Uma falha limpa apenas a pasta nova criada pela operação. Se a limpeza também falhar, UpdateError preserva o caminho que requer limpeza posterior. Não se tenta aplicar um pacote parcialmente extraído.

## Gerar identidade de assinatura

Exemplo de comando para o responsável pelos releases; cria uma pasta nova e não sobrescreve uma identidade existente:

    python tools/sign_release.py --generate-key C:\Keys\LumeRelease

A pasta recebe private-key.pem e public-key.txt. A chave privada é PKCS8 PEM sem senha nesta ferramenta; mantenha-a fora do repositório, dos releases e da pasta distribuída ao usuário, em local protegido pelas permissões da conta e com backup seguro. Nenhuma chave privada de produção é criada automaticamente. A opção --generate-key precisa ser explícita.

Para assinar um ZIP existente:

    python tools/sign_release.py --package Lume-1.0.1.zip --version 1.0.1 --private-key C:\Keys\LumeRelease\private-key.pem --output Lume-1.0.1.json

A ferramenta autentica os bytes do ZIP pelo hash assinado; não atesta que o código contido nele foi revisado. Um arquivo de manifesto existente não é sobrescrito. A distribuição segura da chave pública e a proteção da chave privada continuam sendo responsabilidade do mantenedor.

## Verificação executada

Python 3.12.14 e cryptography 50.0.2 já disponíveis no runtime local. Os testes usam chaves temporárias, ZIPs locais e processos Python da própria ferramenta. Não usam arquivos ou serviços do IDM.

    python -m unittest discover -s tests -p test_updates.py -v

Cobertura: round trip da ferramenta de assinatura, staging sem execução, alteração de metadados/pacote/tamanho, chave ausente/errada, assinatura inválida, dependência criptográfica ausente, regressão de versão, esquema/tamanho do manifesto, mudança do pacote entre verificação e staging, traversal, nomes Windows, colisões, symlinks e quotas. Integração gráfica e distribuição de um release real de produção são verificações separadas.

