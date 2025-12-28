-- Отключение нерелевантных RSS источников
-- Причина: TASS, Habr, Lenta и другие дают ВСЕ новости по технологиям,
-- а не только AI + legal, что приводит к нерелевантному контенту
-- (ДТП, погода, общие IT-новости и т.п.)

-- Отключаем широкие RSS источники
UPDATE sources
SET enabled = false
WHERE name IN (
    'Lenta.ru - Технологии',
    'RBC - Технологии',
    'Interfax - Наука и технологии',
    'TASS - Наука и технологии',
    'Habr - Новости'
);

-- Проверяем результат - должны остаться только целевые источники
-- (Google News RSS с AI+legal фильтрами, Perplexity Search)
SELECT id, name, enabled, quality_score
FROM sources
ORDER BY enabled DESC, quality_score DESC;
