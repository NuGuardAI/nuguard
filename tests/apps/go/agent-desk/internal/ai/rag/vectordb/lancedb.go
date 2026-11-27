//go:build lancedb

package vectordb

import (
	"context"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"

	"agent-desk/internal/pkg/config"

	"github.com/apache/arrow/go/v17/arrow"
	"github.com/apache/arrow/go/v17/arrow/array"
	"github.com/apache/arrow/go/v17/arrow/memory"
	"github.com/lancedb/lancedb-go/pkg/contracts"
	"github.com/lancedb/lancedb-go/pkg/lancedb"
)

const lanceDBVectorColumn = "vector"

type LanceDBProvider struct {
	conn contracts.IConnection
}

func NewLanceDBProvider(cfg *config.LanceDBVectorDBConfig) (Provider, error) {
	if cfg == nil {
		return nil, fmt.Errorf("lancedb config is nil")
	}
	path := strings.TrimSpace(cfg.Path)
	if path == "" {
		path = "data/lancedb"
	}
	if err := os.MkdirAll(path, 0o755); err != nil {
		return nil, fmt.Errorf("failed to create lancedb directory %s: %w", path, err)
	}
	conn, err := lancedb.Connect(context.Background(), path, nil)
	if err != nil {
		return nil, err
	}
	return &LanceDBProvider{conn: conn}, nil
}

func (p *LanceDBProvider) Close() error {
	if p.conn == nil || p.conn.IsClosed() {
		return nil
	}
	return p.conn.Close()
}

func (p *LanceDBProvider) CreateCollection(ctx context.Context, name string, dimension int) error {
	if dimension <= 0 {
		return fmt.Errorf("invalid lancedb vector dimension: %d", dimension)
	}
	if err := p.ensureOpen(); err != nil {
		return err
	}
	schema, err := newLanceDBSchema(dimension)
	if err != nil {
		return err
	}
	table, err := p.conn.CreateTable(ctx, name, schema)
	if err != nil {
		return fmt.Errorf("failed to create lancedb table %s: %w", name, err)
	}
	return table.Close()
}

func (p *LanceDBProvider) DeleteCollection(ctx context.Context, name string) error {
	if err := p.ensureOpen(); err != nil {
		return err
	}
	if err := p.conn.DropTable(ctx, name); err != nil {
		return fmt.Errorf("failed to delete lancedb table %s: %w", name, err)
	}
	return nil
}

func (p *LanceDBProvider) GetCollection(ctx context.Context, name string) (*CollectionInfo, error) {
	table, err := p.openTable(ctx, name)
	if err != nil {
		return nil, err
	}
	defer table.Close()

	schema, err := table.Schema(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get lancedb table schema %s: %w", name, err)
	}
	count, err := table.Count(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to count lancedb table %s: %w", name, err)
	}
	return &CollectionInfo{
		Name:       name,
		Dimension:  lanceDBVectorDimension(schema),
		PointCount: int(count),
		Status:     "ok",
	}, nil
}

func (p *LanceDBProvider) ListCollections(ctx context.Context) ([]string, error) {
	if err := p.ensureOpen(); err != nil {
		return nil, err
	}
	names, err := p.conn.TableNames(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to list lancedb tables: %w", err)
	}
	return names, nil
}

func (p *LanceDBProvider) UpsertVectors(ctx context.Context, collectionName string, vectors []Vector) error {
	if len(vectors) == 0 {
		return nil
	}
	table, err := p.openTable(ctx, collectionName)
	if err != nil {
		return err
	}
	defer table.Close()

	ids := make([]string, 0, len(vectors))
	for _, vector := range vectors {
		if strings.TrimSpace(vector.ID) != "" {
			ids = append(ids, vector.ID)
		}
	}
	if len(ids) > 0 {
		if err := table.Delete(ctx, lanceDBStringInFilter("id", ids)); err != nil {
			return fmt.Errorf("failed to delete existing lancedb vectors from %s: %w", collectionName, err)
		}
	}

	record, release, err := newLanceDBVectorRecord(vectors)
	if err != nil {
		return err
	}
	defer release()

	if err := table.AddRecords(ctx, []arrow.Record{record}, nil); err != nil {
		return fmt.Errorf("failed to add lancedb vectors to %s: %w", collectionName, err)
	}
	return nil
}

func (p *LanceDBProvider) DeleteVectors(ctx context.Context, collectionName string, ids []string) error {
	if len(ids) == 0 {
		return nil
	}
	table, err := p.openTable(ctx, collectionName)
	if err != nil {
		return err
	}
	defer table.Close()

	if err := table.Delete(ctx, lanceDBStringInFilter("id", ids)); err != nil {
		return fmt.Errorf("failed to delete lancedb vectors from %s: %w", collectionName, err)
	}
	return nil
}

func (p *LanceDBProvider) Search(ctx context.Context, req *SearchRequest) ([]SearchResult, error) {
	table, err := p.openTable(ctx, req.CollectionName)
	if err != nil {
		return nil, err
	}
	defer table.Close()

	filter := lanceDBSearchFilter(req.Filter)
	var rows []map[string]interface{}
	if filter == "" {
		rows, err = table.VectorSearch(ctx, lanceDBVectorColumn, req.Vector, req.TopK)
	} else {
		rows, err = table.VectorSearchWithFilter(ctx, lanceDBVectorColumn, req.Vector, req.TopK, filter)
	}
	if err != nil {
		return nil, fmt.Errorf("failed to search lancedb table %s: %w", req.CollectionName, err)
	}

	results := make([]SearchResult, 0, len(rows))
	for _, row := range rows {
		score := lanceDBScoreFromRow(row)
		if req.ScoreThreshold > 0 && score < req.ScoreThreshold {
			continue
		}
		results = append(results, SearchResult{
			ID:      valueToString(row["id"]),
			Score:   score,
			Payload: lanceDBPayloadFromRow(row),
		})
	}
	return results, nil
}

func (p *LanceDBProvider) ensureOpen() error {
	if p == nil || p.conn == nil || p.conn.IsClosed() {
		return fmt.Errorf("lancedb provider is closed")
	}
	return nil
}

func (p *LanceDBProvider) openTable(ctx context.Context, name string) (contracts.ITable, error) {
	if err := p.ensureOpen(); err != nil {
		return nil, err
	}
	table, err := p.conn.OpenTable(ctx, name)
	if err != nil {
		return nil, fmt.Errorf("failed to open lancedb table %s: %w", name, err)
	}
	return table, nil
}

func newLanceDBSchema(dimension int) (contracts.ISchema, error) {
	schema := arrow.NewSchema([]arrow.Field{
		{Name: "id", Type: arrow.BinaryTypes.String, Nullable: false},
		{Name: lanceDBVectorColumn, Type: arrow.FixedSizeListOf(int32(dimension), arrow.PrimitiveTypes.Float32), Nullable: false},
		{Name: "knowledge_base_id", Type: arrow.PrimitiveTypes.Int64, Nullable: false},
		{Name: "document_id", Type: arrow.PrimitiveTypes.Int64, Nullable: false},
		{Name: "document_title", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "faq_id", Type: arrow.PrimitiveTypes.Int64, Nullable: false},
		{Name: "faq_question", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "chunk_no", Type: arrow.PrimitiveTypes.Int32, Nullable: false},
		{Name: "chunk_type", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "section_path", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "title", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "content", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "provider", Type: arrow.BinaryTypes.String, Nullable: true},
	}, nil)
	return lancedb.NewSchema(schema)
}

func newLanceDBVectorRecord(vectors []Vector) (arrow.Record, func(), error) {
	dimension := 0
	for _, item := range vectors {
		if len(item.Vector) > 0 {
			dimension = len(item.Vector)
			break
		}
	}
	if dimension <= 0 {
		return nil, nil, fmt.Errorf("lancedb vector dimension is empty")
	}
	for _, item := range vectors {
		if len(item.Vector) != dimension {
			return nil, nil, fmt.Errorf("inconsistent lancedb vector dimension for %s: got %d, want %d", item.ID, len(item.Vector), dimension)
		}
	}

	pool := memory.NewGoAllocator()
	idBuilder := array.NewStringBuilder(pool)
	kbIDBuilder := array.NewInt64Builder(pool)
	documentIDBuilder := array.NewInt64Builder(pool)
	documentTitleBuilder := array.NewStringBuilder(pool)
	faqIDBuilder := array.NewInt64Builder(pool)
	faqQuestionBuilder := array.NewStringBuilder(pool)
	chunkNoBuilder := array.NewInt32Builder(pool)
	chunkTypeBuilder := array.NewStringBuilder(pool)
	sectionPathBuilder := array.NewStringBuilder(pool)
	titleBuilder := array.NewStringBuilder(pool)
	contentBuilder := array.NewStringBuilder(pool)
	providerBuilder := array.NewStringBuilder(pool)
	vectorBuilder := array.NewFloat32Builder(pool)

	for _, item := range vectors {
		payload := item.Payload
		idBuilder.Append(item.ID)
		vectorBuilder.AppendValues(item.Vector, nil)
		kbIDBuilder.Append(payload.KnowledgeBaseID)
		documentIDBuilder.Append(payload.DocumentID)
		documentTitleBuilder.Append(payload.DocumentTitle)
		faqIDBuilder.Append(payload.FaqID)
		faqQuestionBuilder.Append(payload.FaqQuestion)
		chunkNoBuilder.Append(int32(payload.ChunkNo))
		chunkTypeBuilder.Append(payload.ChunkType)
		sectionPathBuilder.Append(payload.SectionPath)
		titleBuilder.Append(payload.Title)
		contentBuilder.Append(payload.Content)
		providerBuilder.Append(payload.Provider)
	}

	idArray := idBuilder.NewArray()
	vectorValues := vectorBuilder.NewArray()
	kbIDArray := kbIDBuilder.NewArray()
	documentIDArray := documentIDBuilder.NewArray()
	documentTitleArray := documentTitleBuilder.NewArray()
	faqIDArray := faqIDBuilder.NewArray()
	faqQuestionArray := faqQuestionBuilder.NewArray()
	chunkNoArray := chunkNoBuilder.NewArray()
	chunkTypeArray := chunkTypeBuilder.NewArray()
	sectionPathArray := sectionPathBuilder.NewArray()
	titleArray := titleBuilder.NewArray()
	contentArray := contentBuilder.NewArray()
	providerArray := providerBuilder.NewArray()

	vectorType := arrow.FixedSizeListOf(int32(dimension), arrow.PrimitiveTypes.Float32)
	vectorArray := array.NewFixedSizeListData(
		array.NewData(vectorType, len(vectors), []*memory.Buffer{nil}, []arrow.ArrayData{vectorValues.Data()}, 0, 0),
	)
	schema := arrow.NewSchema([]arrow.Field{
		{Name: "id", Type: arrow.BinaryTypes.String, Nullable: false},
		{Name: lanceDBVectorColumn, Type: vectorType, Nullable: false},
		{Name: "knowledge_base_id", Type: arrow.PrimitiveTypes.Int64, Nullable: false},
		{Name: "document_id", Type: arrow.PrimitiveTypes.Int64, Nullable: false},
		{Name: "document_title", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "faq_id", Type: arrow.PrimitiveTypes.Int64, Nullable: false},
		{Name: "faq_question", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "chunk_no", Type: arrow.PrimitiveTypes.Int32, Nullable: false},
		{Name: "chunk_type", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "section_path", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "title", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "content", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "provider", Type: arrow.BinaryTypes.String, Nullable: true},
	}, nil)
	columns := []arrow.Array{
		idArray,
		vectorArray,
		kbIDArray,
		documentIDArray,
		documentTitleArray,
		faqIDArray,
		faqQuestionArray,
		chunkNoArray,
		chunkTypeArray,
		sectionPathArray,
		titleArray,
		contentArray,
		providerArray,
	}
	record := array.NewRecord(schema, columns, int64(len(vectors)))
	release := func() {
		record.Release()
		for _, column := range columns {
			column.Release()
		}
		vectorValues.Release()
	}
	return record, release, nil
}

func lanceDBVectorDimension(schema *arrow.Schema) int {
	if schema == nil {
		return 0
	}
	for i := 0; i < schema.NumFields(); i++ {
		field := schema.Field(i)
		if field.Name != lanceDBVectorColumn {
			continue
		}
		listType, ok := field.Type.(*arrow.FixedSizeListType)
		if !ok {
			return 0
		}
		return int(listType.Len())
	}
	return 0
}

func lanceDBSearchFilter(filter *SearchFilter) string {
	if filter == nil {
		return ""
	}
	parts := make([]string, 0, 2)
	if len(filter.KnowledgeBaseIDs) > 0 {
		parts = append(parts, lanceDBIntInFilter("knowledge_base_id", filter.KnowledgeBaseIDs))
	}
	if len(filter.DocumentIDs) > 0 {
		parts = append(parts, lanceDBIntInFilter("document_id", filter.DocumentIDs))
	}
	return strings.Join(parts, " AND ")
}

func lanceDBIntInFilter(column string, values []int64) string {
	items := make([]string, 0, len(values))
	for _, value := range values {
		items = append(items, strconv.FormatInt(value, 10))
	}
	return fmt.Sprintf("%s IN (%s)", column, strings.Join(items, ","))
}

func lanceDBStringInFilter(column string, values []string) string {
	items := make([]string, 0, len(values))
	for _, value := range values {
		items = append(items, "'"+strings.ReplaceAll(value, "'", "''")+"'")
	}
	return fmt.Sprintf("%s IN (%s)", column, strings.Join(items, ","))
}

func lanceDBScoreFromRow(row map[string]interface{}) float32 {
	for _, key := range []string{"_distance", "distance"} {
		if value, ok := row[key]; ok {
			distance := valueToFloat64(value)
			if math.IsNaN(distance) {
				break
			}
			score := 1 - distance
			if score < 0 {
				return 0
			}
			if score > 1 {
				return 1
			}
			return float32(score)
		}
	}
	for _, key := range []string{"_score", "score"} {
		if value, ok := row[key]; ok {
			score := valueToFloat64(value)
			if !math.IsNaN(score) {
				return float32(score)
			}
		}
	}
	return 0
}

func lanceDBPayloadFromRow(row map[string]interface{}) ChunkPayload {
	return ChunkPayload{
		KnowledgeBaseID: valueToInt64(row["knowledge_base_id"]),
		DocumentID:      valueToInt64(row["document_id"]),
		DocumentTitle:   valueToString(row["document_title"]),
		FaqID:           valueToInt64(row["faq_id"]),
		FaqQuestion:     valueToString(row["faq_question"]),
		ChunkNo:         int(valueToInt64(row["chunk_no"])),
		ChunkType:       valueToString(row["chunk_type"]),
		SectionPath:     valueToString(row["section_path"]),
		Title:           valueToString(row["title"]),
		Content:         valueToString(row["content"]),
		Provider:        valueToString(row["provider"]),
	}
}

func valueToString(value interface{}) string {
	switch v := value.(type) {
	case nil:
		return ""
	case string:
		return v
	case []byte:
		return string(v)
	default:
		return fmt.Sprint(value)
	}
}

func valueToInt64(value interface{}) int64 {
	switch v := value.(type) {
	case int:
		return int64(v)
	case int32:
		return int64(v)
	case int64:
		return v
	case uint64:
		return int64(v)
	case float32:
		return int64(v)
	case float64:
		return int64(v)
	case string:
		ret, _ := strconv.ParseInt(v, 10, 64)
		return ret
	default:
		return 0
	}
}

func valueToFloat64(value interface{}) float64 {
	switch v := value.(type) {
	case float32:
		return float64(v)
	case float64:
		return v
	case int:
		return float64(v)
	case int32:
		return float64(v)
	case int64:
		return float64(v)
	case string:
		ret, err := strconv.ParseFloat(v, 64)
		if err == nil {
			return ret
		}
	}
	return math.NaN()
}
