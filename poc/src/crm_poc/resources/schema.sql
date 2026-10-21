CREATE TABLE poc_item (
    item_id INTEGER PRIMARY KEY,
    item_name TEXT NOT NULL
);

INSERT INTO poc_item (item_id, item_name) VALUES (1, 'bundled-sql-ok');
