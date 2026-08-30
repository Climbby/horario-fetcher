"""Parser checks for catalogo.py. Run with: python test_catalogo.py

No test framework on purpose: this runs in the crawl workflow before a single
request goes out, so a broken parser never gets to overwrite a good catalogue.
"""

import sys

from catalogo import header_columns, parse_programme, parse_unit
from ucshared import normalize_semester

from bs4 import BeautifulSoup

# The main study plan: the semester lives in "Regime" and there is no code.
MAIN_PLAN = """
<table class="sp-table"><thead><tr>
  <th>Nome da unidade curricular</th><th>Ano</th><th>Regime</th>
  <th>Tipo</th><th>Área Científica</th><th>Créditos ECTS</th>
</tr></thead><tbody>
  <tr><td><a href="/courses/PT/unit/9836/25081/2026-2027">Análise Matemática I</a></td>
      <td>1</td><td>1º Semestre</td><td>Obrigatória</td><td>MAT</td><td>6.0</td></tr>
</tbody></table>
"""

# An optional group: an extra "Código" column, and the semester moves to
# "Duração". Read by position, the code lands in the semester field.
OPTIONAL_GROUP = """
<table><thead><tr>
  <th>Nome da unidade curricular</th><th>Ano</th><th>Código da unidade curricular</th>
  <th>Duração</th><th>Tipo</th><th>Área Científica</th><th>Créditos ECTS</th>
</tr></thead><tbody>
  <tr><td><a href="/courses/PT/unit/97327/27443/2026-2027">A Pessoa com Deficiência</a></td>
      <td>4</td><td>97327</td><td>1º Semestre</td><td>Opcional</td><td>ENF</td><td>2.0</td></tr>
</tbody></table>
"""

UNIT_PAGE = """
<div><strong class="uk-form-label">Ano</strong><br><span>3</span></div>
<div><strong>Código</strong><br><span>{code}</span></div>
"""

def check(name, got, want):
    if got != want:
        print(f"FALHOU {name}\n  esperado: {want!r}\n  obtido:   {got!r}")
        return 1
    print(f"ok  {name}")
    return 0


def main():
    failures = 0

    main_rows = parse_programme(MAIN_PLAN)
    failures += check("plano principal: uma cadeira", len(main_rows), 1)
    failures += check("plano principal: semestre", main_rows[0]["semester"], "1º Semestre")
    failures += check("plano principal: ano", main_rows[0]["curricular_year"], 1)
    failures += check("plano principal: sem código na tabela", main_rows[0]["code"], None)
    failures += check("plano principal: obrigatória", main_rows[0]["is_optional"], False)
    failures += check("plano principal: unit id", main_rows[0]["uc_unit_id"], 9836)

    group_rows = parse_programme(OPTIONAL_GROUP)
    failures += check("grupo: uma cadeira", len(group_rows), 1)
    # Sem mapear pelo cabeçalho, isto seria "97327".
    failures += check("grupo: Duração vira semestre", group_rows[0]["semester"], "1º Semestre")
    failures += check("grupo: código vem da tabela", group_rows[0]["code"], "97327")
    failures += check("grupo: opcional", group_rows[0]["is_optional"], True)

    both = parse_programme(MAIN_PLAN + OPTIONAL_GROUP)
    failures += check("os dois layouts na mesma página", len(both), 2)

    failures += check("tabela sem cabeçalho é ignorada",
                      header_columns(BeautifulSoup("<table><tr><td>x</td></tr></table>",
                                                   "html.parser").table), None)

    # 01000010 na maioria dos cursos, 97431 na Escola Superior de Enfermagem.
    failures += check("código de 8 dígitos", parse_unit(UNIT_PAGE.format(code="01022365"))["code"], "01022365")
    failures += check("código de 5 dígitos", parse_unit(UNIT_PAGE.format(code="97431"))["code"], "97431")
    failures += check("código absurdo é recusado", parse_unit(UNIT_PAGE.format(code="n/a")), None)

    failures += check("semestre do InforEstudante", normalize_semester("1.º Semestre"), "1º Semestre")
    failures += check("semestre do apps.uc.pt", normalize_semester("1º Semestre"), "1º Semestre")

    print(f"\n{failures} falha(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
