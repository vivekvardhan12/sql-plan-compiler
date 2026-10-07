-- queries.sql
-- ===========
-- Demo queries that all compile successfully. Each one shows a different
-- feature of the compiler. Run them all with:
--     python main.py --file examples/queries.sql

-- 1. Simplest query: read a whole table
SELECT * FROM employees;

-- 2. Choose columns and filter rows
SELECT name, salary FROM employees WHERE salary > 50000;

-- 3. AND binds tighter than OR: read as  age < 25 OR (age > 55 AND salary > 80000)
SELECT name FROM employees WHERE age < 25 OR age > 55 AND salary > 80000;

-- 4. Sorting (the sort column does not have to be in the SELECT list)
SELECT name FROM employees ORDER BY age DESC;

-- 5. Join without filters: both inputs are large, so the planner picks a Hash Join
SELECT name, dept_name FROM employees JOIN departments ON dept_id = departments.id;

-- 6. Join with filters on both tables: predicate pushdown, then a Nested Loop Join
SELECT name, dept_name FROM employees
JOIN departments ON dept_id = departments.id
WHERE salary > 50000 AND location = 'Hyderabad' AND age < 40
ORDER BY salary DESC;

-- 7. An OR that uses both tables cannot be pushed below the join
SELECT name FROM employees JOIN departments ON dept_id = departments.id
WHERE age < 30 OR location = 'Pune';

-- 8. Constant folding: '1 = 1' is decided at compile time and removed
SELECT name FROM employees WHERE age > 30 AND 1 = 1;
