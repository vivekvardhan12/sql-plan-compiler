-- errors.sql
-- ==========
-- Every query here is WRONG on purpose, to demonstrate the error messages
-- of each compiler phase. Run them all with:
--     python main.py --file examples/errors.sql

-- 1. Lexer error: '@' is not a valid character in our SQL
SELECT name FROM @employees;

-- 2. Lexer error: SQL strings use single quotes, not double quotes
SELECT name FROM employees WHERE name = "Alice";

-- 3. Parser error: trailing comma in the column list
SELECT name, FROM employees;

-- 4. Parser error: missing comparison operator
SELECT * FROM employees WHERE salary 50000;

-- 5. Parser error: LIMIT is not supported
SELECT * FROM employees LIMIT 5;

-- 6. Semantic error: misspelled table (watch the "did you mean" hint)
SELECT * FROM employes;

-- 7. Semantic error: misspelled column
SELECT salry FROM employees;

-- 8. Semantic error: 'id' exists in both joined tables, so it is ambiguous
SELECT id FROM employees JOIN departments ON dept_id = departments.id;

-- 9. Semantic error: comparing a number with text
SELECT name FROM employees WHERE salary > 'high';

-- 10. Semantic error: dept_name belongs to departments, which is not in this query
SELECT dept_name FROM employees;
