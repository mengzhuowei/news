ALTER TABLE articles ADD FULLTEXT INDEX ft_search (title, summary, content);
