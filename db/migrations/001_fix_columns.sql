-- Убираем жёсткие лимиты на текстовые поля, где могут быть длинные значения
ALTER TABLE authors       ALTER COLUMN name       TYPE TEXT;
ALTER TABLE attributes    ALTER COLUMN value      TYPE TEXT;

ALTER TABLE products ALTER COLUMN publisher       TYPE VARCHAR(500);
ALTER TABLE products ALTER COLUMN binding         TYPE VARCHAR(500);
ALTER TABLE products ALTER COLUMN category        TYPE VARCHAR(500);
ALTER TABLE products ALTER COLUMN age_restriction TYPE VARCHAR(100);
ALTER TABLE offers   ALTER COLUMN title           TYPE TEXT;
