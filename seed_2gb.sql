-- 1. Создаем начальные ссылки (50 000 уникальных коротких ссылок)
INSERT INTO short_urls (
    short_code, 
    long_url, 
    date_created, 
    custom_slug_was_provided, 
    max_visits, 
    crawlable, 
    forward_query
)
SELECT 
    'code_' || i, 
    'https://example.com/target/page/' || i || '/' || md5(random()::text), 
    NOW() - (random() * interval '30 days'), 
    false, 
    100000, 
    false, 
    true
FROM generate_series(1, 50000) AS i
ON CONFLICT DO NOTHING;

-- 2. Генерируем 3 500 000 визитов с метаданными (основной объем данных)
-- Пакетная вставка большими блоками
INSERT INTO visits (
    referer, 
    date, 
    user_agent, 
    type, 
    short_url_id, 
    visited_url, 
    potential_bot
)
SELECT 
    'https://referrer-' || (i % 500) || '.com/source/campaign/' || (i % 50),
    NOW() - ((i % 1000) * interval '1 minute'),
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' || (100 + (i % 25)) || '.0.0.0 Safari/537.36',
    'regular',
    (SELECT id FROM short_urls LIMIT 1 OFFSET (i % 50000)),
    'https://api.localhost/code_' || (i % 50000),
    (i % 10 = 0)
FROM generate_series(1, 3500000) AS i;

-- 3. Генерируем геолокации для половины визитов (таблица visit_locations)
INSERT INTO visit_locations (
    country_code, 
    country_name, 
    region_name, 
    city_name, 
    timezone, 
    lat, 
    lon, 
    visit_id
)
SELECT 
    CASE (i % 4) 
        WHEN 0 THEN 'DE' 
        WHEN 1 THEN 'US' 
        WHEN 2 THEN 'FR' 
        ELSE 'GB' 
    END,
    CASE (i % 4) 
        WHEN 0 THEN 'Germany' 
        WHEN 1 THEN 'United States' 
        WHEN 2 THEN 'France' 
        ELSE 'United Kingdom' 
    END,
    'Region-' || (i % 20),
    'City-' || (i % 100),
    'UTC',
    52.5200 + ((i % 100) * 0.01),
    13.4050 + ((i % 100) * 0.01),
    i
FROM generate_series(1, 1500000) AS i;