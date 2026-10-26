# Previna — Service Desk

Aplicação de suporte em Flask + SQLite. Esta entrega consolida a Fase 0: indicadores consistentes, filtros por período, notificações, acessibilidade básica e isolamento de dados demonstrativos.

## Funcionalidades

- Login com senha em hash; solicitantes veem somente os próprios chamados e a equipe de TI vê a fila.
- Abertura de chamados, comentários, atualização de status por atendente e registro de histórico em comentários.
- Indicadores mutuamente exclusivos: Total, Abertos, Em andamento, Aguardando usuário e Resolvidos/encerrados.
- Filtro único de período aplicado aos KPIs, contagens das abas e lista.
- Cards clicáveis e contagens por status.
- Sino com contador e dropdown das notificações recentes, página completa, toasts e aviso sonoro após interação do usuário com o navegador.
- Atalhos configuráveis da base de conhecimento, contato do plantão e horário de suporte.
- Proteção CSRF, consultas SQL parametrizadas, cookies HTTP-only e autorização do lado do servidor.
- Migração SQLite aditiva: tabelas novas e colunas demonstrativas não apagam os chamados ou usuários existentes.

## Instalação e início no Windows

Requisitos: Windows 10/11 ou Windows Server compatível e Python 3.11+.

1. Execute \`INICIAR_LOCAL.bat\` ou abra PowerShell na pasta e rode:
   \`\`\`powershell
   .\.venv\Scripts\python.exe app.py
   \`\`\`
2. Acesse \`http://127.0.0.1:5000\`.
3. Não existe senha padrão. Crie o primeiro usuário de TI:
   \`\`\`powershell
   .\.venv\Scripts\python.exe -m flask --app app:create_app create-user
   \`\`\`
   Use um nome de usuário, nome completo, perfil \`ti\` e uma senha com pelo menos 10 caracteres. Para criar um solicitante, execute o mesmo comando e selecione o perfil \`solicitante\`.

Para listar contas sem expor hashes:
\`\`\`powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app list-users
\`\`\`

## Testes automatizados

\`\`\`powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
\`\`\`

A suíte cobre permissões, login, comentários, filtros e notificações. Inclui a regra crítica: 1 chamado Em andamento + 1 Encerrado = Total 2, Abertos 0, Em andamento 1, Aguardando usuário 0 e Resolvidos/encerrados 1.

## Dados de demonstração — isolados por padrão

O sistema não cria dados fictícios automaticamente. Para demonstrar, use **um arquivo SQLite exclusivo**, diferente do banco real, e defina as variáveis somente nessa sessão PowerShell:

\`\`\`powershell
$env:PREVINA_DATABASE = "$PWD\instance\demonstracao.sqlite3"
$env:DEMO_MODE = "true"
$env:DEMO_SEED_PASSWORD = "substitua-por-uma-senha-forte"
.\.venv\Scripts\python.exe -m flask --app app:create_app seed-demo-data
\`\`\`

A senha de demonstração precisa ter pelo menos 10 caracteres. Não habilite \`DEMO_MODE=true\` com o banco de produção. No ambiente de demonstração, a limpeza dos registros marcados é feita com backup automático antes de apagar:

\`\`\`powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app clean-demo-data
\`\`\`

O comando de seed e o de limpeza falham quando \`DEMO_MODE\` não está habilitado. A limpeza remove apenas registros marcados como demonstrativos e cria um backup \`*.bak_AAAAMMDD_HHMMSS_ffffff\`. Preserve esse backup até confirmar que não é necessário restaurar.

## Configuração

- \`PREVINA_DATABASE\`: caminho completo do banco SQLite; use banco separado para demonstração.
- \`FLASK_SECRET_KEY\`: segredo definido pelo administrador; se ausente, é gerado em \`instance/.secret_key\`.
- \`HOST\` e \`PORT\`: host/porta na inicialização pelo \`app.py\`; padrão \`127.0.0.1:5000\`.
- \`DEMO_MODE\`: habilita seed e limpeza demonstrativos somente com valor verdadeiro.
- \`DEMO_SEED_PASSWORD\`: senha inicial dos usuários demonstrativos.
- \`SUPPORT_KNOWLEDGE_BASE_URL\`: URL configurável da base de conhecimento.
- \`SUPPORT_ON_CALL_URL\`: URL configurável para contato do plantão.
- \`SUPPORT_HOURS_TEXT\`: texto com horário oficial do suporte.
- \`SESSION_COOKIE_SECURE=true\`: habilitar atrás de HTTPS.

## Limites operacionais

O padrão continua escutando em \`127.0.0.1\`. Não exponha diretamente à rede corporativa ou à internet sem servidor WSGI, HTTPS, regras de firewall, backups verificados e teste de implantação. O banco padrão permanece em \`instance/chamados.sqlite3\`. A migração desta fase é aditiva e não apaga usuários nem chamados existentes.
