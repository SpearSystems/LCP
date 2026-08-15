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
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write(CanonicalSigningBytes(timestamp, idempotencyKey, body))
	return hex.EncodeToString(mac.Sum(nil))
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
	for attempt := 0; attempt <= c.MaxRetries; attempt++ {
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
			if attempt == c.MaxRetries {
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
		if !contains([]int{429, 500, 502, 503, 504}, response.StatusCode) || attempt == c.MaxRetries {
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
