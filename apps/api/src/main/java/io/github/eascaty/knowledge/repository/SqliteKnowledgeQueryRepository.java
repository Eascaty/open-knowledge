package io.github.eascaty.knowledge.repository;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.eascaty.knowledge.api.KnowledgeStoreException;
import io.github.eascaty.knowledge.config.KnowledgeProperties;
import io.github.eascaty.knowledge.domain.HealthStatus;
import io.github.eascaty.knowledge.domain.KnowledgeDocument;
import io.github.eascaty.knowledge.domain.KnowledgeDocumentSummary;
import io.github.eascaty.knowledge.domain.KnowledgeSearchHit;
import io.github.eascaty.knowledge.domain.KnowledgeSource;
import io.github.eascaty.knowledge.domain.PageResult;
import io.github.eascaty.knowledge.domain.RelationEdge;
import io.github.eascaty.knowledge.domain.TaxonomyNode;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Properties;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Repository;

@Repository
public class SqliteKnowledgeQueryRepository implements KnowledgeQueryRepository {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            SqliteKnowledgeQueryRepository.class);
    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() { };
    private static final String PUBLIC_DOCUMENT_FILTER = "d.visibility = 'public'";
    private static final Set<Integer> SUPPORTED_SCHEMA_VERSIONS = Set.of(1, 2);

    private final Path databasePath;
    private final ObjectMapper objectMapper;

    public SqliteKnowledgeQueryRepository(KnowledgeProperties properties, ObjectMapper objectMapper) {
        this.databasePath = properties.getDatabasePath().toAbsolutePath().normalize();
        this.objectMapper = objectMapper;
    }

    @Override
    public HealthStatus health() {
        if (!Files.isRegularFile(databasePath)) {
            LOGGER.warn("Knowledge database health check failed: configured path is not a regular file");
            return new HealthStatus("DOWN", "personal-knowledge-service", "unavailable", null);
        }
        try (Connection connection = openReadOnly();
             PreparedStatement statement = connection.prepareStatement(
                     "SELECT value FROM metadata WHERE key = 'schema_version'");
            ResultSet result = statement.executeQuery()) {
            Integer schemaVersion = result.next() ? Integer.valueOf(result.getString(1)) : null;
            String status = schemaVersion != null
                    && SUPPORTED_SCHEMA_VERSIONS.contains(schemaVersion) ? "UP" : "DOWN";
            return new HealthStatus(status, "personal-knowledge-service", "available", schemaVersion);
        } catch (SQLException exception) {
            LOGGER.warn(
                    "Knowledge database health check failed: SQL state={}, error code={}",
                    exception.getSQLState(),
                    exception.getErrorCode());
            return new HealthStatus("DOWN", "personal-knowledge-service", "unavailable", null);
        } catch (NumberFormatException exception) {
            LOGGER.warn("Knowledge database health check failed: schema version is not numeric");
            return new HealthStatus("DOWN", "personal-knowledge-service", "unavailable", null);
        }
    }

    @Override
    public List<TaxonomyNode> findTaxonomy() {
        String sql = """
                SELECT id, parent_id, name, path_json, locked
                FROM nodes
                WHERE active = 1
                ORDER BY level, sort_order, id
                """;
        try (Connection connection = openReadOnly();
             PreparedStatement statement = connection.prepareStatement(sql);
             ResultSet result = statement.executeQuery()) {
            Map<String, NodeRow> rows = new LinkedHashMap<>();
            while (result.next()) {
                NodeRow row = new NodeRow(
                        result.getString("id"),
                        result.getString("parent_id"),
                        result.getString("name"),
                        readStringList(result.getString("path_json")),
                        result.getBoolean("locked"));
                rows.put(row.id(), row);
            }
            List<TaxonomyNode> roots = new ArrayList<>();
            for (NodeRow row : rows.values()) {
                if (row.parentId() == null || !rows.containsKey(row.parentId())) {
                    roots.add(buildTree(row, rows));
                }
            }
            return List.copyOf(roots);
        } catch (SQLException exception) {
            throw storeFailure(exception);
        }
    }

    @Override
    public PageResult<KnowledgeDocumentSummary> findDocuments(String query, int page, int size) {
        String normalizedQuery = query == null ? "" : query.strip();
        String pattern = "%" + escapeLike(normalizedQuery) + "%";
        String searchFilter = normalizedQuery.isEmpty()
                ? ""
                : " AND (d.title LIKE ? ESCAPE '\\' OR d.summary LIKE ? ESCAPE '\\' "
                        + "OR d.body LIKE ? ESCAPE '\\' OR d.tags_json LIKE ? ESCAPE '\\')";
        String countSql = "SELECT COUNT(*) FROM documents d WHERE "
                + PUBLIC_DOCUMENT_FILTER + searchFilter;
        String dataSql = """
                SELECT d.id, d.title, d.summary, d.tags_json, p.node_id,
                       n.path_json, d.updated_at
                FROM documents d
                JOIN placements p ON p.document_id = d.id
                JOIN nodes n ON n.id = p.node_id AND n.active = 1
                WHERE %s%s
                ORDER BY d.updated_at DESC, d.id
                LIMIT ? OFFSET ?
                """.formatted(PUBLIC_DOCUMENT_FILTER, searchFilter);
        try (Connection connection = openReadOnly()) {
            long total;
            try (PreparedStatement count = connection.prepareStatement(countSql)) {
                int index = bindSearch(count, normalizedQuery, pattern, 1);
                if (index < 1) {
                    throw new IllegalStateException("invalid parameter index");
                }
                try (ResultSet result = count.executeQuery()) {
                    total = result.next() ? result.getLong(1) : 0;
                }
            }
            List<KnowledgeDocumentSummary> items = new ArrayList<>();
            try (PreparedStatement data = connection.prepareStatement(dataSql)) {
                int index = bindSearch(data, normalizedQuery, pattern, 1);
                data.setInt(index++, size);
                data.setLong(index, (long) page * size);
                try (ResultSet result = data.executeQuery()) {
                    while (result.next()) {
                        items.add(toSummary(result));
                    }
                }
            }
            return new PageResult<>(page, size, total, List.copyOf(items));
        } catch (SQLException exception) {
            throw storeFailure(exception);
        }
    }

    @Override
    public PageResult<KnowledgeSearchHit> searchDocuments(String query, int page, int size) {
        String normalizedQuery = query == null ? "" : query.strip();
        if (normalizedQuery.isEmpty()) {
            return new PageResult<>(page, size, 0, List.of());
        }
        try (Connection connection = openReadOnly()) {
            if (supportsTrigramSearch(connection, normalizedQuery)) {
                PageResult<KnowledgeSearchHit> result = searchWithFts(
                        connection, normalizedQuery, page, size);
                if (result.total() > 0) {
                    return result;
                }
            }
            return searchWithLike(connection, normalizedQuery, page, size);
        } catch (SQLException exception) {
            throw storeFailure(exception);
        }
    }

    @Override
    public Optional<KnowledgeDocument> findDocument(String id) {
        String sql = """
                SELECT d.id, d.title, d.body, d.summary, d.tags_json, p.node_id,
                       n.path_json, d.updated_at, s.kind, s.original_name, s.sha256
                FROM documents d
                JOIN sources s ON s.id = d.source_id
                JOIN placements p ON p.document_id = d.id
                JOIN nodes n ON n.id = p.node_id AND n.active = 1
                WHERE d.id = ? AND %s
                """.formatted(PUBLIC_DOCUMENT_FILTER);
        try (Connection connection = openReadOnly();
             PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, id);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) {
                    return Optional.empty();
                }
                KnowledgeSource source = new KnowledgeSource(
                        result.getString("kind"),
                        result.getString("original_name"),
                        result.getString("sha256"));
                return Optional.of(new KnowledgeDocument(
                        result.getString("id"),
                        result.getString("title"),
                        result.getString("body"),
                        result.getString("summary"),
                        readStringList(result.getString("tags_json")),
                        result.getString("node_id"),
                        readStringList(result.getString("path_json")),
                        result.getString("updated_at"),
                        source,
                        findRelations(connection, id)));
            }
        } catch (SQLException exception) {
            throw storeFailure(exception);
        }
    }

    private Connection openReadOnly() throws SQLException {
        if (!Files.isRegularFile(databasePath)) {
            throw new SQLException("knowledge database does not exist");
        }
        Properties properties = new Properties();
        properties.setProperty("open_mode", "1");
        properties.setProperty("foreign_keys", "true");
        String encodedPath = databasePath.toUri().getRawPath();
        return DriverManager.getConnection(
                "jdbc:sqlite:file:" + encodedPath + "?mode=ro&immutable=1",
                properties);
    }

    private boolean supportsTrigramSearch(Connection connection, String query)
            throws SQLException {
        if (query.codePointCount(0, query.length()) < 3 || query.contains("\"")) {
            return false;
        }
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT value FROM metadata WHERE key = 'fts_tokenizer'");
             ResultSet result = statement.executeQuery()) {
            return result.next() && "trigram".equals(result.getString(1));
        }
    }

    private PageResult<KnowledgeSearchHit> searchWithFts(
            Connection connection, String query, int page, int size) throws SQLException {
        String expression = "\"" + query.replace("\"", "\"\"") + "\"";
        String countSql = """
                SELECT COUNT(*)
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.document_id
                JOIN placements p ON p.document_id = d.id
                JOIN nodes n ON n.id = p.node_id AND n.active = 1
                WHERE documents_fts MATCH ? AND %s
                """.formatted(PUBLIC_DOCUMENT_FILTER);
        long total;
        try (PreparedStatement count = connection.prepareStatement(countSql)) {
            count.setString(1, expression);
            try (ResultSet result = count.executeQuery()) {
                total = result.next() ? result.getLong(1) : 0;
            }
        }
        if (total == 0) {
            return new PageResult<>(page, size, 0, List.of());
        }
        String dataSql = """
                SELECT d.id, d.title, d.body, d.summary, d.tags_json, p.node_id,
                       n.path_json, d.updated_at,
                       bm25(documents_fts, 0.0, 8.0, 1.0, 4.0, 2.0) AS rank
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.document_id
                JOIN placements p ON p.document_id = d.id
                JOIN nodes n ON n.id = p.node_id AND n.active = 1
                WHERE documents_fts MATCH ? AND %s
                ORDER BY rank, d.updated_at DESC, d.id
                LIMIT ? OFFSET ?
                """.formatted(PUBLIC_DOCUMENT_FILTER);
        List<KnowledgeSearchHit> items = new ArrayList<>();
        try (PreparedStatement data = connection.prepareStatement(dataSql)) {
            data.setString(1, expression);
            data.setInt(2, size);
            data.setLong(3, (long) page * size);
            try (ResultSet result = data.executeQuery()) {
                while (result.next()) {
                    items.add(toSearchHit(result, query));
                }
            }
        }
        return new PageResult<>(page, size, total, List.copyOf(items));
    }

    private PageResult<KnowledgeSearchHit> searchWithLike(
            Connection connection, String query, int page, int size) throws SQLException {
        String pattern = "%" + escapeLike(query) + "%";
        String filter = """
                 AND (d.title LIKE ? ESCAPE '\\' OR d.summary LIKE ? ESCAPE '\\'
                      OR d.body LIKE ? ESCAPE '\\' OR d.tags_json LIKE ? ESCAPE '\\'
                      OR n.path_json LIKE ? ESCAPE '\\')
                """;
        String countSql = """
                SELECT COUNT(*)
                FROM documents d
                JOIN placements p ON p.document_id = d.id
                JOIN nodes n ON n.id = p.node_id AND n.active = 1
                WHERE %s%s
                """.formatted(PUBLIC_DOCUMENT_FILTER, filter);
        long total;
        try (PreparedStatement count = connection.prepareStatement(countSql)) {
            bindLikePattern(count, pattern, 1, 5);
            try (ResultSet result = count.executeQuery()) {
                total = result.next() ? result.getLong(1) : 0;
            }
        }
        String dataSql = """
                SELECT d.id, d.title, d.body, d.summary, d.tags_json, p.node_id,
                       n.path_json, d.updated_at,
                       CASE
                         WHEN d.title LIKE ? ESCAPE '\\' THEN 0.0
                         WHEN d.tags_json LIKE ? ESCAPE '\\' THEN 1.0
                         WHEN n.path_json LIKE ? ESCAPE '\\' THEN 2.0
                         WHEN d.summary LIKE ? ESCAPE '\\' THEN 3.0
                         ELSE 4.0
                       END AS rank
                FROM documents d
                JOIN placements p ON p.document_id = d.id
                JOIN nodes n ON n.id = p.node_id AND n.active = 1
                WHERE %s%s
                ORDER BY rank, d.updated_at DESC, d.id
                LIMIT ? OFFSET ?
                """.formatted(PUBLIC_DOCUMENT_FILTER, filter);
        List<KnowledgeSearchHit> items = new ArrayList<>();
        try (PreparedStatement data = connection.prepareStatement(dataSql)) {
            int index = bindLikePattern(data, pattern, 1, 4);
            index = bindLikePattern(data, pattern, index, 5);
            data.setInt(index++, size);
            data.setLong(index, (long) page * size);
            try (ResultSet result = data.executeQuery()) {
                while (result.next()) {
                    items.add(toSearchHit(result, query));
                }
            }
        }
        return new PageResult<>(page, size, total, List.copyOf(items));
    }

    private int bindLikePattern(
            PreparedStatement statement, String pattern, int startIndex, int count)
            throws SQLException {
        int index = startIndex;
        for (int field = 0; field < count; field++) {
            statement.setString(index++, pattern);
        }
        return index;
    }

    private List<RelationEdge> findRelations(Connection connection, String documentId)
            throws SQLException {
        String sql = """
                SELECT id, from_node_id, to_node_id, relation_type, label, confidence
                FROM relations
                WHERE document_id = ?
                ORDER BY id
                """;
        try (PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setString(1, documentId);
            try (ResultSet result = statement.executeQuery()) {
                List<RelationEdge> relations = new ArrayList<>();
                while (result.next()) {
                    relations.add(new RelationEdge(
                            result.getString("id"),
                            result.getString("from_node_id"),
                            result.getString("to_node_id"),
                            result.getString("relation_type"),
                            result.getString("label"),
                            result.getDouble("confidence")));
                }
                return List.copyOf(relations);
            }
        }
    }

    private TaxonomyNode buildTree(NodeRow row, Map<String, NodeRow> rows) {
        List<TaxonomyNode> children = rows.values().stream()
                .filter(candidate -> row.id().equals(candidate.parentId()))
                .map(candidate -> buildTree(candidate, rows))
                .toList();
        return new TaxonomyNode(
                row.id(), row.parentId(), row.name(), row.path(), row.locked(), children);
    }

    private KnowledgeDocumentSummary toSummary(ResultSet result) throws SQLException {
        return new KnowledgeDocumentSummary(
                result.getString("id"),
                result.getString("title"),
                result.getString("summary"),
                readStringList(result.getString("tags_json")),
                result.getString("node_id"),
                readStringList(result.getString("path_json")),
                result.getString("updated_at"));
    }

    private KnowledgeSearchHit toSearchHit(ResultSet result, String query) throws SQLException {
        String title = result.getString("title");
        String body = result.getString("body");
        String summary = result.getString("summary");
        List<String> tags = readStringList(result.getString("tags_json"));
        List<String> path = readStringList(result.getString("path_json"));
        return new KnowledgeSearchHit(
                result.getString("id"),
                title,
                highlightedFragment(title, query, 160),
                summary,
                highlightedFragment(firstMatchingText(query, body, summary,
                        String.join(" / ", tags), String.join(" / ", path)), query, 220),
                tags,
                result.getString("node_id"),
                path,
                result.getDouble("rank"),
                result.getString("updated_at"));
    }

    private String firstMatchingText(String query, String... candidates) {
        for (String candidate : candidates) {
            if (indexOfIgnoreCase(candidate, query) >= 0) {
                return candidate;
            }
        }
        return candidates.length == 0 ? "" : candidates[0];
    }

    private String highlightedFragment(String text, String query, int maximumLength) {
        String compact = text == null ? "" : text.replaceAll("\\s+", " ").strip();
        if (compact.isEmpty()) {
            return "";
        }
        int match = indexOfIgnoreCase(compact, query);
        int context = Math.max(20, (maximumLength - query.length()) / 2);
        int start = match < 0 ? 0 : Math.max(0, match - context);
        int end = Math.min(compact.length(), start + maximumLength);
        if (match >= 0 && match + query.length() > end) {
            end = Math.min(compact.length(), match + query.length() + context);
        }
        String fragment = compact.substring(start, end);
        String prefix = start > 0 ? "…" : "";
        String suffix = end < compact.length() ? "…" : "";
        int localMatch = match < 0 ? -1 : match - start;
        if (localMatch < 0 || localMatch + query.length() > fragment.length()) {
            return prefix + escapeHtml(fragment) + suffix;
        }
        return prefix
                + escapeHtml(fragment.substring(0, localMatch))
                + "[["
                + escapeHtml(fragment.substring(localMatch, localMatch + query.length()))
                + "]]"
                + escapeHtml(fragment.substring(localMatch + query.length()))
                + suffix;
    }

    private int indexOfIgnoreCase(String text, String query) {
        if (text == null || query == null || query.length() > text.length()) {
            return -1;
        }
        for (int index = 0; index <= text.length() - query.length(); index++) {
            if (text.regionMatches(true, index, query, 0, query.length())) {
                return index;
            }
        }
        return -1;
    }

    private String escapeHtml(String value) {
        return value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&#39;");
    }

    private int bindSearch(
            PreparedStatement statement, String query, String pattern, int startIndex)
            throws SQLException {
        int index = startIndex;
        if (!query.isEmpty()) {
            for (int field = 0; field < 4; field++) {
                statement.setString(index++, pattern);
            }
        }
        return index;
    }

    private List<String> readStringList(String json) {
        try {
            return List.copyOf(objectMapper.readValue(json, STRING_LIST));
        } catch (JsonProcessingException exception) {
            throw new KnowledgeStoreException("知识库 JSON 字段无法读取", exception);
        }
    }

    private String escapeLike(String value) {
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }

    private KnowledgeStoreException storeFailure(SQLException exception) {
        return new KnowledgeStoreException("知识库只读查询暂不可用", exception);
    }

    private record NodeRow(
            String id,
            String parentId,
            String name,
            List<String> path,
            boolean locked) {
    }
}
