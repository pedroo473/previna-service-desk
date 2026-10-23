# Previna — Central de Chamados de TI (MVP)

Aplicação interna para registrar e acompanhar solicitações de suporte. MVP em Flask + SQLite, sem serviços externos.

## O que já funciona

- Login com senha armazenada como hash (não em texto puro).
- Perfil **solicitante**: abre chamados, visualiza apenas os próprios e adiciona comentários.
- Perfil **TI**: visualiza os chamados de todos, comenta e atualiza status.
- Prioridade, categoria, título, descrição, datas e histórico de comentários/status.
- Busca por número/título/categoria e filtros por status.
- Proteção CSRF nos formulários, consultas SQL parametrizadas, cookies HTTP-only e validação de permissões no servidor.
- Base SQLite criada automaticamente em `instance/chamados.sqlite3`.
- Endpoint local de verificação: `http://127.0.0.1:5000/health`.

## Requisitos

- Windows 10/11 ou Windows Server compatível.
- Python 3.11 ou superior recomendado.
- Acesso à internet na primeira instalação para baixar os pacotes Python.

## Primeira execução no Windows

1. Extraia a pasta `previna_chamados` em um local permanente, por exemplo `C:\Sistemas\previna_chamados`.
2. Abra a pasta no VS Code ou execute `INICIAR_LOCAL.bat` com duplo clique.
3. Na primeira execução, o script cria `.venv` e instala as dependências. Aguarde a conclusão.
4. Quando aparecer que o servidor está rodando, abra `http://127.0.0.1:5000`.
5. Deixe a janela do terminal aberta enquanto usa o sistema.

A primeira inicialização cria o banco vazio. **Não existe usuário ou senha padrão.** Crie o primeiro usuário com perfil de TI seguindo as instruções abaixo.

## Criar o primeiro usuário de TI

Pare o servidor com `Ctrl+C`. No terminal do VS Code, estando dentro da pasta do projeto, execute:

```powershell
.\.venv\Scripts\python.exe -m flask --app app.py create-user
```

Preencha o usuário, nome completo, perfil `ti` e uma senha com pelo menos 10 caracteres (a senha será solicitada sem aparecer na tela e com confirmação). Em seguida, inicie novamente:

```powershell
.\.venv\Scripts\python.exe app.py
```

Depois de entrar com a conta TI, crie contas para os funcionários usando o mesmo comando `create-user`, mas escolha o perfil `solicitante`.

Para listar contas sem exibir hashes de senha:

```powershell
.\.venv\Scripts\python.exe -m flask --app app.py list-users
```

## Executar os testes automatizados

Instale as dependências e execute:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Estrutura

```text
previna_chamados/
├── app.py                 # rotas, autenticação e permissões
├── schema.sql             # estrutura SQLite e índices
├── requirements.txt       # dependências
├── INICIAR_LOCAL.bat      # instalação e execução local no Windows
├── templates/             # páginas HTML Jinja
├── static/style.css       # interface responsiva, sem CDN obrigatório
├── tests/test_app.py      # testes de regressão
└── instance/              # criado automaticamente; banco e chave local
```

## Segurança e limites desta primeira versão

- O servidor inicia em `127.0.0.1` (somente no próprio computador). Mantenha assim durante os testes e não o exponha diretamente à internet.
- **Não habilite acesso pela rede da empresa ainda.** Antes do uso por funcionários, a próxima etapa deve configurar hospedagem apropriada (servidor WSGI), HTTPS, regras de entrada do Firewall do Windows limitadas à rede corporativa, backup e teste em ambiente controlado. Não transmita credenciais por HTTP em uma rede compartilhada.
- A conta inicial com perfil `ti` deve ser criada localmente por quem administra o sistema. Não há cadastro público.
- Não registre senhas, dados médicos ou dados pessoais desnecessários na descrição/comentários.
- Faça backup de `instance/chamados.sqlite3` com o processo parado, ou use uma estratégia de backup consistente. Guarde `instance/.secret_key` junto do ambiente: perdê-la invalida as sessões existentes.
- Não há ainda anexos, redefinição de senha, auditoria administrativa completa, atribuição de técnico, notificações por e-mail/WhatsApp, nem gestão de contas pela interface.

## Configuração opcional

- `PREVINA_DATABASE`: caminho completo alternativo para o arquivo SQLite.
- `FLASK_SECRET_KEY`: segredo definido pelo administrador; quando ausente, é gerada e guardada uma chave local em `instance/.secret_key`.
- `HOST` e `PORT`: endereço e porta de escuta; por padrão `127.0.0.1:5000`.
