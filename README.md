# HorarioFetcher

Lê o teu horário do [InforEstudante](https://inforestudante.uc.pt) e guarda-o num
ficheiro `classes_filtered.json`, para depois montares o horário nos
[Horários UC](https://climbby.github.io/horarioPicker/).

Funciona com **qualquer curso da UC** — Medicina, Filosofia, Economia, o que for.
O curso e o semestre são escolhidos quando corres a ferramenta.

O catálogo de cadeiras da UC (que cursos existem, que cadeiras têm, de que ano
e de que semestre) é público e é recolhido pelo `catalogo.py`. Os **horários**
não são: só existem atrás do login, e por isso é que esta ferramenta tem de
correr na tua máquina. Ao importares o ficheiro no site podes partilhá-lo, e as
cadeiras ficam disponíveis para toda a gente que as tenha.

---

## Para quem só quer usar (sem instalar nada)

1. **Instala o Google Chrome**, se ainda não o tiveres.
   ([google.com/chrome](https://www.google.com/chrome))
2. Descarrega o `HorarioFetcher.exe` da página de
   [Releases](https://github.com/Climbby/horario-fetcher/releases).
3. Corre o ficheiro.
   > O Windows pode mostrar "O Windows protegeu o seu PC" — é por o programa não
   > estar assinado digitalmente. Carrega em **Mais informações → Executar mesmo assim**.
4. Escreve o teu utilizador do InforEstudante e a password.
   A password **não fica gravada em lado nenhum** — é escrita só nesse momento e
   enviada apenas para o inforestudante.uc.pt.
5. Escolhe o curso (só pergunta se tiveres mais do que uma matrícula) e o semestre.
6. Quando acabar, fica um `classes_filtered.json` na mesma pasta do `.exe`.
7. Abre os [Horários UC](https://climbby.github.io/horarioPicker/), carrega em
   **Importar ficheiro** e escolhe esse ficheiro. A caixa de partilha vem
   ligada: o site diz-te, antes de enviares, a que curso e ano é que cada
   cadeira pertence.

O horário fica guardado no teu browser, por isso nas próximas visitas já aparece
sozinho.

---

## Para quem prefere correr o código

```bash
git clone https://github.com/Climbby/horario-fetcher.git
cd horario-fetcher
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python horariofetcher.py
```

### Correr sem perguntas (opcional)

Cria um ficheiro `.env` ao lado do script. Com o utilizador **e** a password
preenchidos, a ferramenta não faz nenhuma pergunta:

```ini
UC_USERNAME=uc2024123456@student.uc.pt
UC_PASSWORD=a-tua-password
UC_SEMESTER=1.º Semestre
HORARIO_PICKER_JSON=/caminho/para/classes_filtered.json
```

Todas as linhas são opcionais. O `.env` está no `.gitignore` e nunca vai para o repositório.

| Variável | Efeito |
|---|---|
| `UC_USERNAME` / `UC_PASSWORD` | Salta o pedido de credenciais |
| `UC_SEMESTER` | Salta o menu do semestre (tem de existir na tua matrícula) |
| `UC_ACADEMIC_YEAR` | Força o ano letivo, ex: `2026-2027` (por defeito é deduzido) |
| `HORARIO_PICKER_JSON` | Onde gravar o JSON (por defeito, ao lado da ferramenta) |
| `HORARIO_HEADLESS=1` | Corre sem abrir janela do Chrome |
| `HORARIO_KEEP_BROWSER=1` | Deixa o Chrome aberto no fim, para debug |

As preferências da última execução (utilizador, curso, semestre) ficam num
`fetcher_settings.json` local, também fora do repositório. A password nunca lá entra.

---

## Gerar um novo `.exe`

O PyInstaller não faz cross-compile, por isso o `.exe` é construído no Windows pelo
GitHub Actions (`.github/workflows/build-exe.yml`), não na tua máquina.

- **Publicar uma versão:** cria uma tag e faz push — o `.exe` aparece sozinho em Releases.
  ```bash
  git tag v1.0.0 && git push origin v1.0.0
  ```
- **Só testar o build:** no separador Actions do GitHub, corre o workflow
  *Build Windows exe* à mão e descarrega o artefacto.

---

## Ficheiros

| Ficheiro | O que faz |
|---|---|
| `horariofetcher.py` | Lê o horário do InforEstudante e escreve o `classes_filtered.json` |
| `catalogo.py` | Rastreia o catálogo público em `apps.uc.pt` e enche a base de dados |
| `ucshared.py` | As convenções que os dois têm de partilhar (ano letivo, semestre) |

---

## O catálogo

O `apps.uc.pt` publica o plano de estudos de toda a Universidade sem login, e o
campo **Código** de cada cadeira é o mesmo `class_id` que o fetcher grava. É
isso que permite saber a que curso e a que ano pertence cada cadeira de um
upload, sem ter de perguntar nada a quem o faz.

```bash
python catalogo.py --course 362 --out catalogo.json   # só LEI, sem escrever na BD
python catalogo.py --push                             # tudo, para o Supabase
```

As duas variáveis do `--push` vêm do mesmo `.env` (nunca da linha de comandos,
que fica no histórico da shell):

```ini
SUPABASE_URL=https://xxxxxxxx.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_...
```

Corre sozinho uma vez por semana no GitHub Actions
(`.github/workflows/crawl-catalog.yml`), com as mesmas duas nos secrets. Faz no máximo 2 pedidos por segundo e
guarda em cache o código de cada unidade entre execuções.
