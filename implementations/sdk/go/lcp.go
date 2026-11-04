// Package lcp provides an idiomatic Go client and signing helpers for LCP.
package lcp

import (
	"bytes"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"
)

type Message struct {
	ID             string  `json:"id"`
	Type           string  `json:"type"`
	Timestamp      string  `json:"timestamp"`
	SenderID       string  `json:"sender_id"`
	ReceiverID     string  `json:"receiver_id"`
	CorrelationID  *string `json:"correlation_id"`
	IdempotencyKey string  `json:"idempotency_key"`
	Test           bool    `json:"test"`
}

type Envelope struct {
	LCP struct {
		Version string          `json:"version"`
		Message Message         `json:"message"`
		Payload json.RawMessage `json:"payload"`
	} `json:"lcp"`
}

func BuildEnvelope(messageType, senderID, receiverID string, payload any) (Envelope, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return Envelope{}, err
	}
	now := time.Now().UTC().Format(time.RFC3339)
	envelope := Envelope{}
	envelope.LCP.Version = "1.0.0"
	envelope.LCP.Message = Message{ID: newUUID(), Type: messageType, Timestamp: now, SenderID: senderID, ReceiverID: receiverID, IdempotencyKey: fmt.Sprintf("%s-%s-%s", senderID, messageType, newUUID()), Test: false}
	envelope.LCP.Payload = body
	return envelope, nil
}

func newUUID() string {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic(err)
	}
	value[6] = (value[6] & 0x0f) | 0x40
	value[8] = (value[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", value[0:4], value[4:6], value[6:8], value[8:10], value[10:16])
}

func CanonicalSigningBytes(timestamp, idempotencyKey string, body []byte) []byte {
	return append([]byte(timestamp+"\n"+idempotencyKey+"\n"), body...)
}

func SignHMAC(secret, timestamp, idempotencyKey string, body []byte) string {
	return hmacHex(secret, CanonicalSigningBytes(timestamp, idempotencyKey, body))
}

// hmacHex is the package's single HMAC-SHA256 → lowercase-hex construction
// (SPEC.md §9). Signing and contact hashes share it; nothing else computes a
// keyed digest.
func hmacHex(secret string, data []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write(data)
	return hex.EncodeToString(mac.Sum(nil))
}

// NormalizeContact returns the SPEC.md §9 pinned contact preimage for a
// value. Phone: the wire value verbatim, byte-for-byte — no folding,
// stripping, or reformatting (the reference platform's phone_hash builder is
// the executed baseline). Email: the §9 pinned candidate canonical — trim
// surrounding whitespace, ASCII-lowercase the domain, keep the local-part
// bytes verbatim, plus-addresses distinct. Email-form contacted_hashes
// ENTRIES stay emission-gated until #153 T9 executed vectors publish; this is
// the pinned compute rule, so every contact-hash vector row is reproducible.
func NormalizeContact(value, domain string) (string, error) {
	switch domain {
	case "phone":
		return value, nil
	case "email":
		trimmed := strings.TrimSpace(value)
		separator := strings.LastIndex(trimmed, "@")
		if separator < 0 {
			return "", fmt.Errorf("email contact preimage must contain '@'")
		}
		local := trimmed[:separator]
		host := strings.Map(func(r rune) rune {
			if r >= 'A' && r <= 'Z' {
				return r + 32
			}
			return r
		}, trimmed[separator+1:])
		return local + "@" + host, nil
	default:
		return "", fmt.Errorf("unknown contact domain %q (expected phone or email)", domain)
	}
}

// ContactHash returns the per-pair contact hash (SPEC.md §9, LEP-0003 §3):
// lowercase-hex HMAC-SHA256(pair_secret, normalized_preimage) keyed with the
// secret shared with the message's receiver. phone_hash, email_hash, and
// contacted_hashes entries all ride this one construction — the same digest
// path as SignHMAC, never an unsalted digest, never SHA-1.
func ContactHash(secret, value, domain string) (string, error) {
	preimage, err := NormalizeContact(value, domain)
	if err != nil {
		return "", err
	}
	return hmacHex(secret, []byte(preimage)), nil
}

func VerifyHMAC(secret, signature, timestamp, idempotencyKey string, body []byte, maxSkew time.Duration, now time.Time) error {
	signedAt, err := time.Parse(time.RFC3339, timestamp)
	if err != nil || signedAt.IsZero() {
		return errors.New("invalid X-LCP-Timestamp")
	}
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if now.Sub(signedAt) > maxSkew || signedAt.Sub(now) > maxSkew {
		return errors.New("X-LCP-Timestamp is outside the replay window")
	}
	expected := SignHMAC(secret, timestamp, idempotencyKey, body)
	if len(signature) != len(expected) || subtle.ConstantTimeCompare([]byte(strings.ToLower(signature)), []byte(expected)) != 1 {
		return errors.New("invalid X-LCP-Signature")
	}
	return nil
}

func VerifyHTTPHeaders(secret string, headers http.Header, body []byte, maxSkew time.Duration, now time.Time) error {
	signature := headers.Get("X-LCP-Signature")
	timestamp := headers.Get("X-LCP-Timestamp")
	key := headers.Get("X-LCP-Idempotency-Key")
	if signature == "" || timestamp == "" || key == "" {
		return errors.New("missing required LCP authentication headers")
	}
	return VerifyHMAC(secret, signature, timestamp, key, body, maxSkew, now)
}

type HTTPError struct {
	Status int
	Body   any
}

func (e *HTTPError) Error() string { return fmt.Sprintf("LCP HTTP %d", e.Status) }

type Client struct {
	Endpoint   string
	SenderID   string
	APIKey     string
	HMACSecret string
	HTTP       *http.Client
	MaxRetries int
}

func NewClient(endpoint string) *Client {
	return &Client{Endpoint: strings.TrimRight(endpoint, "/"), HTTP: &http.Client{Timeout: 30 * time.Second}, MaxRetries: 2}
}

func (c *Client) Request(method, path string, payload any, test bool) (map[string]any, error) {
	return c.request(method, path, payload, test, c.MaxRetries)
}

func (c *Client) request(method, path string, payload any, test bool, maxRetries int) (map[string]any, error) {
	if maxRetries < 0 {
		maxRetries = 0
	}
	var body []byte
	var err error
	if payload != nil {
		body, err = json.Marshal(payload)
		if err != nil {
			return nil, err
		}
	}
	idempotencyKey := ""
	if envelope, ok := payload.(Envelope); ok {
		idempotencyKey = envelope.LCP.Message.IdempotencyKey
	}
	if idempotencyKey == "" && len(body) > 0 {
		var raw map[string]any
		if json.Unmarshal(body, &raw) == nil {
			if lcpValue, ok := raw["lcp"].(map[string]any); ok {
				if msg, ok := lcpValue["message"].(map[string]any); ok {
					idempotencyKey, _ = msg["idempotency_key"].(string)
				}
			}
		}
	}
	for attempt := 0; attempt <= maxRetries; attempt++ {
		req, err := http.NewRequest(method, c.Endpoint+"/"+strings.TrimLeft(path, "/"), bytes.NewReader(body))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json")
		if c.SenderID != "" {
			req.Header.Set("X-LCP-Sender-Id", c.SenderID)
		}
		if c.APIKey != "" {
			req.Header.Set("Authorization", "Bearer "+c.APIKey)
		}
		if idempotencyKey != "" {
			req.Header.Set("X-LCP-Idempotency-Key", idempotencyKey)
		}
		if test {
			req.Header.Set("X-LCP-Test", "true")
		}
		if c.HMACSecret != "" {
			timestamp := time.Now().UTC().Format(time.RFC3339)
			req.Header.Set("X-LCP-Timestamp", timestamp)
			req.Header.Set("X-LCP-Signature", SignHMAC(c.HMACSecret, timestamp, idempotencyKey, body))
		}
		response, err := c.HTTP.Do(req)
		if err != nil {
			if attempt == maxRetries {
				return nil, err
			}
			time.Sleep(time.Duration(1<<attempt) * time.Second)
			continue
		}
		responseBody, readErr := io.ReadAll(response.Body)
		response.Body.Close()
		if readErr != nil {
			return nil, readErr
		}
		var result map[string]any
		if len(responseBody) > 0 {
			if json.Unmarshal(responseBody, &result) != nil {
				result = map[string]any{"raw": string(responseBody)}
			}
		} else {
			result = map[string]any{}
		}
		if response.StatusCode >= 200 && response.StatusCode < 300 {
			return result, nil
		}
		if !contains([]int{429, 500, 502, 503, 504}, response.StatusCode) || attempt == maxRetries {
			return nil, &HTTPError{Status: response.StatusCode, Body: result}
		}
		time.Sleep(time.Duration(1<<attempt) * time.Second)
	}
	return nil, errors.New("LCP request failed")
}

func contains(values []int, target int) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
func (c *Client) SubmitLead(payload Envelope, test bool) (map[string]any, error) {
	return c.Request(http.MethodPost, "/v1/lcp/leads", payload, test)
}
func (c *Client) SubmitCall(payload Envelope, test bool) (map[string]any, error) {
	return c.Request(http.MethodPost, "/v1/lcp/calls", payload, test)
}
func (c *Client) SubmitBid(payload Envelope, test bool) (map[string]any, error) {
	return c.Request(http.MethodPost, "/v1/lcp/bids", payload, test)
}

// MaxBatchItems caps one batch submission (LEP-0001 W2-01, schemas/batch.json).
const MaxBatchItems = 100

// BatchItem is one lead in a batch submission: the per-item identifiers plus
// the full lead payload (schemas/lead.json root), never an envelope.
type BatchItem struct {
	ItemID         string `json:"item_id"`
	IdempotencyKey string `json:"idempotency_key"`
	Payload        any    `json:"payload"`
}

// SubmitLeadsBatch submits up to MaxBatchItems leads in one request and
// returns the per-item receipts in input order. HTTP 207 partial success is a
// successful response, per-item error objects are preserved, and the batch is
// never retried as a whole — retrying only rejected/error receipts is the
// caller's job.
func (c *Client) SubmitLeadsBatch(items []BatchItem, requestID string) ([]map[string]any, error) {
	if len(items) == 0 || len(items) > MaxBatchItems {
		return nil, fmt.Errorf("batch supports 1..%d items, got %d", MaxBatchItems, len(items))
	}
	if requestID == "" {
		requestID = newUUID()
	}
	response, err := c.request(http.MethodPost, "/v1/lcp/leads/batch", map[string]any{"request_id": requestID, "items": items}, false, 0)
	if err != nil {
		return nil, err
	}
	raw, ok := response["results"].([]any)
	if !ok {
		return nil, errors.New("batch response missing results")
	}
	order := make(map[string]int, len(items))
	for index, item := range items {
		order[item.ItemID] = index
	}
	results := make([]map[string]any, 0, len(raw))
	for _, entry := range raw {
		result, ok := entry.(map[string]any)
		if !ok {
			return nil, errors.New("batch response contained a non-object result")
		}
		results = append(results, result)
	}
	rank := func(result map[string]any) int {
		itemID, _ := result["item_id"].(string)
		if index, ok := order[itemID]; ok {
			return index
		}
		return len(items)
	}
	sort.SliceStable(results, func(left, right int) bool { return rank(results[left]) < rank(results[right]) })
	return results, nil
}

func (c *Client) QueryLeadStatus(id string) (map[string]any, error) {
	return c.Request(http.MethodGet, "/v1/lcp/leads/"+url.PathEscape(id), nil, false)
}
func (c *Client) GetSchema(name string) (map[string]any, error) {
	return c.Request(http.MethodGet, "/v1/lcp/schemas/"+url.PathEscape(name), nil, false)
}
func (c *Client) GetCapabilities() (map[string]any, error) {
	return c.Request(http.MethodGet, "/v1/lcp/capabilities", nil, false)
}
func (c *Client) ListOffers(vertical string) (map[string]any, error) {
	path := "/v1/lcp/offers"
	if vertical != "" {
		path += "?vertical=" + url.QueryEscape(vertical)
	}
	return c.Request(http.MethodGet, path, nil, false)
}

// HistoryParams filters a LEP-0005 request-history read
// (GET /v1/lcp/history/requests, history:read). A zero Limit and empty strings
// are omitted from the query rather than sent blank — a blank filter would
// widen the page instead of narrowing it. Rows are scoped server-side to the
// credential's tenant, so there is no tenant field.
type HistoryParams struct {
	Limit           int
	Cursor          string
	RequestID       string
	Status          string
	Vertical        string
	CallerPhoneHash string
	From            string
	To              string
}

// query renders the supplied filters as a query string. url.Values.Encode
// sorts by name, so the result is stable for a given filter set.
func (p HistoryParams) query() string {
	values := url.Values{}
	if p.Limit > 0 {
		values.Set("limit", fmt.Sprintf("%d", p.Limit))
	}
	for name, value := range map[string]string{
		"cursor":            p.Cursor,
		"request_id":        p.RequestID,
		"status":            p.Status,
		"vertical":          p.Vertical,
		"caller_phone_hash": p.CallerPhoneHash,
		"from":              p.From,
		"to":                p.To,
	} {
		if value != "" {
			values.Set(name, value)
		}
	}
	return values.Encode()
}

// RequestHistory pages the LEP-0005 request-history audit log and returns the
// decoded response body unchanged ({"requests": [...]}). A 4xx surfaces as
// *HTTPError through the shared request path.
func (c *Client) RequestHistory(params HistoryParams) (map[string]any, error) {
	path := "/v1/lcp/history/requests"
	if query := params.query(); query != "" {
		path += "?" + query
	}
	return c.Request(http.MethodGet, path, nil, false)
}
