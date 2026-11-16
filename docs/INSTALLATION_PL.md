# Instrukcja instalacji (PL)

## A. Wgraj pliki
1. Rozpakuj ZIP.
2. Skopiuj katalogi `agent`, `docs`, `tests`, `.github` oraz `README.md` i `.gitignore` do katalogu głównego repozytorium.
3. Nie kopiuj ani nie nadpisuj żadnego `index.html`.
4. Zacommituj pliki do `main` (albo otwórz osobny PR z instalacją).

## B. Dodaj sekret API
1. Wejdź na https://platform.deepseek.com/ i utwórz API key.
2. W repozytorium GitHub otwórz Settings → Secrets and variables → Actions.
3. Kliknij New repository secret.
4. Nazwa: `DEEPSEEK_API_KEY`.
5. Wartość: Twój klucz API DeepSeek.
6. Zapisz.

Nie wpisuj klucza do plików repozytorium ani aplikacji webowej.

## C. Uprawnienia Actions
1. Settings → Actions → General.
2. W sekcji Workflow permissions wybierz `Read and write permissions`.
3. Zapisz.
4. Workflow ma jawne `contents: write` i `pull-requests: write`.
5. Jeżeli repozytorium ma politykę organizacji blokującą zapisy Actions, administrator musi ją dopuścić.

## D. Pierwszy test
1. Otwórz zakładkę Actions.
2. Wybierz `AltCards — DeepSeek PR`.
3. Kliknij Run workflow.
4. Gałąź: `main`.
5. Wpisz małą zmianę CSS, np.:
   `Improve the spacing between the Settings and Shuffle buttons on the study screen on narrow mobile screens. CSS only. Do not change JavaScript behavior, SRS, XP, daily limits, Supabase, authentication, or data structures.`
6. `allow_sensitive_changes`: pozostaw false.
7. Uruchom workflow.
8. Po sukcesie otwórz utworzony Pull Request i sprawdź diff.
9. Przetestuj aplikację na telefonie i desktopie. Dopiero potem merge.

## E. Zmiany w logice krytycznej
Domyślnie `allow_sensitive_changes=false`. Włącz true tylko wtedy, gdy zadanie dotyczy konkretnie logiki SRS, XP, dziennego limitu, zapisu/synchronizacji lub auth. W opisie zadania wskaż dokładny obszar i wymagane zachowanie. Nadal obowiązuje ręczna akceptacja PR.

## F. Co oznaczają błędy
- `Could not identify a safe code area`: doprecyzuj ekran/funkcję/klasę CSS.
- `find text occurs 0 or more than once`: zmniejsz zakres zadania; agent nie znalazł jednoznacznego miejsca.
- `Protected AltCards logic changed`: agent próbował zmienić krytyczny obszar, a zgoda nie została włączona.
- `QA did not pass`: przeczytaj findings w logach Actions i podziel zadanie.
- `No changes to publish`: agent nie wygenerował zmiany lub operacja była identyczna.
- `Resource not accessible by integration`: sprawdź uprawnienia Actions i ustawienia repozytorium.

## G. Ważne
Pull Request jest tworzony automatycznie, ale merge i deploy nie. Nie dodawaj klucza Supabase Service Role. Nie wyłączaj RLS dla ułatwienia pracy agenta.
