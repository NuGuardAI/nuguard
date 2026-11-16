package retrieval

import (
	"errors"
	"path/filepath"
	"strings"
)

var ErrInvalidChunkerConfig = errors.New("invalid chunker configuration")

type Chunk struct {
	Ordinal int
	Content string
	Locator string
}

type Chunker struct {
	maxTokens    int
	overlapRatio float64
}

func NewChunker(maxTokens int, overlapRatio float64) *Chunker {
	return &Chunker{maxTokens: maxTokens, overlapRatio: overlapRatio}
}

func (c *Chunker) Split(document Document) ([]Chunk, error) {
	if c.maxTokens <= 0 || c.overlapRatio < 0 || c.overlapRatio > 0.15 {
		return nil, ErrInvalidChunkerConfig
	}

	sections := document.Sections
	if len(sections) == 0 {
		sections = markdownSections(document.Filename, document.Text)
	}
	chunks := make([]Chunk, 0, len(sections))
	for _, section := range sections {
		for _, content := range c.splitSection(section.Text) {
			chunks = append(chunks, Chunk{
				Ordinal: len(chunks),
				Content: content,
				Locator: section.Locator,
			})
		}
	}
	if len(chunks) == 0 {
		return nil, ErrEmptyDocument
	}
	return chunks, nil
}

func (c *Chunker) splitSection(text string) []string {
	units := naturalUnits(text)
	if len(units) == 0 {
		return nil
	}
	if tokenCount(text) <= c.maxTokens {
		return []string{strings.Join(strings.Fields(text), " ")}
	}

	chunks := make([]string, 0)
	current := make([]string, 0)
	currentTokens := 0
	flush := func() {
		if len(current) == 0 {
			return
		}
		chunks = append(chunks, strings.Join(current, " "))
		current = current[:0]
		currentTokens = 0
	}
	for _, unit := range units {
		unitTokens := tokenCount(unit)
		if unitTokens > c.maxTokens {
			flush()
			chunks = append(chunks, c.splitWords(strings.Fields(unit))...)
			continue
		}
		if currentTokens > 0 && currentTokens+unitTokens > c.maxTokens {
			flush()
		}
		current = append(current, unit)
		currentTokens += unitTokens
	}
	flush()
	return chunks
}

func (c *Chunker) splitWords(words []string) []string {
	overlap := int(float64(c.maxTokens) * c.overlapRatio)
	step := c.maxTokens - overlap
	if step < 1 {
		step = 1
	}
	chunks := make([]string, 0, (len(words)+step-1)/step)
	for start := 0; start < len(words); start += step {
		end := start + c.maxTokens
		if end > len(words) {
			end = len(words)
		}
		chunks = append(chunks, strings.Join(words[start:end], " "))
		if end == len(words) {
			break
		}
	}
	return chunks
}

func naturalUnits(text string) []string {
	units := make([]string, 0)
	for _, line := range strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		start := 0
		for index, character := range line {
			if !isSentenceTerminator(character) {
				continue
			}
			end := index + len(string(character))
			unit := strings.TrimSpace(line[start:end])
			if unit != "" {
				units = append(units, unit)
			}
			start = end
		}
		if remainder := strings.TrimSpace(line[start:]); remainder != "" {
			units = append(units, remainder)
		}
	}
	return units
}

func isSentenceTerminator(character rune) bool {
	switch character {
	case '.', '?', '!', '。', '？', '！':
		return true
	default:
		return false
	}
}

func markdownSections(filename, text string) []DocumentSection {
	defaultLocator := strings.TrimSuffix(filepath.Base(filename), filepath.Ext(filename))
	if defaultLocator == "" {
		defaultLocator = "Document"
	}
	headings := make([]string, 0, 4)
	current := make([]string, 0)
	sections := make([]DocumentSection, 0)

	flush := func() {
		content := strings.TrimSpace(strings.Join(current, "\n"))
		if content == "" {
			current = current[:0]
			return
		}
		locator := defaultLocator
		if len(headings) > 0 {
			locator = strings.Join(headings, " / ")
		}
		sections = append(sections, DocumentSection{Locator: locator, Text: content})
		current = current[:0]
	}

	for _, line := range strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n") {
		if level, title, ok := markdownHeading(line); ok {
			flush()
			if level > len(headings)+1 {
				level = len(headings) + 1
			}
			if level <= len(headings) {
				headings = headings[:level-1]
			}
			headings = append(headings, title)
			continue
		}
		if strings.TrimSpace(line) == "" {
			if len(current) > 0 {
				current = append(current, "")
			}
			continue
		}
		current = append(current, strings.TrimSpace(line))
	}
	flush()
	return sections
}

func markdownHeading(line string) (int, string, bool) {
	trimmed := strings.TrimSpace(line)
	level := 0
	for level < len(trimmed) && level < 6 && trimmed[level] == '#' {
		level++
	}
	if level == 0 || len(trimmed) <= level || trimmed[level] != ' ' {
		return 0, "", false
	}
	title := strings.TrimSpace(trimmed[level:])
	if title == "" {
		return 0, "", false
	}
	return level, title, true
}

func tokenCount(text string) int {
	return len(strings.Fields(text))
}
