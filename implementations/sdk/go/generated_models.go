// Code generated from schemas/; DO NOT EDIT.
package lcp

import "encoding/json"

type MessageModel struct {
	ID             string          `json:"id"`
	Type           string          `json:"type"`
	Timestamp      string          `json:"timestamp"`
	SenderID       string          `json:"sender_id"`
	ReceiverID     string          `json:"receiver_id"`
	CorrelationID  *string         `json:"correlation_id"`
	IdempotencyKey string          `json:"idempotency_key"`
	Test           bool            `json:"test,omitempty"`
	Security       json.RawMessage `json:"security,omitempty"`
}
type EnvelopeModel struct {
	LCP struct {
		Version string          `json:"version"`
		Message MessageModel    `json:"message"`
		Payload json.RawMessage `json:"payload"`
	} `json:"lcp"`
}
type LeadPayload struct {
	LeadID        string          `json:"lead_id"`
	ExternalID    string          `json:"external_id,omitempty"`
	SubmittedAt   string          `json:"submitted_at,omitempty"`
	Status        string          `json:"status"`
	Channel       string          `json:"channel"`
	Consumer      json.RawMessage `json:"consumer"`
	Location      json.RawMessage `json:"location"`
	Compliance    json.RawMessage `json:"compliance,omitempty"`
	Provenance    json.RawMessage `json:"provenance,omitempty"`
	Attributes    map[string]any  `json:"attributes"`
	Exclusivity   json.RawMessage `json:"exclusivity,omitempty"`
	ContactWindow json.RawMessage `json:"contact_window,omitempty"`
	LeadQuality   json.RawMessage `json:"lead_quality,omitempty"`
	Expiry        json.RawMessage `json:"expiry,omitempty"`
	Attachments   []map[string]any `json:"attachments,omitempty"`
}
type CallPayload struct {
	LeadID      string          `json:"lead_id"`
	ExternalID  string          `json:"external_id,omitempty"`
	SubmittedAt string          `json:"submitted_at,omitempty"`
	Status      string          `json:"status"`
	Channel     string          `json:"channel"`
	Consumer    json.RawMessage `json:"consumer"`
	Location    json.RawMessage `json:"location"`
	Call        json.RawMessage `json:"call"`
	Compliance  json.RawMessage `json:"compliance,omitempty"`
	Provenance  json.RawMessage `json:"provenance,omitempty"`
	Attributes  map[string]any  `json:"attributes,omitempty"`
	Attachments []map[string]any `json:"attachments,omitempty"`
}
type PingPayload struct {
	PingID          string         `json:"ping_id"`
	LeadReference   string         `json:"lead_reference"`
	PublisherID     string         `json:"publisher_id,omitempty"`
	OfferID         string         `json:"offer_id,omitempty"`
	PhoneHash       string         `json:"phone_hash"`
	EmailHash       string         `json:"email_hash,omitempty"`
	CountryCode     string         `json:"country_code"`
	Vertical        string         `json:"vertical"`
	Attributes      map[string]any `json:"attributes,omitempty"`
	FloorPriceCents int            `json:"floor_price_cents"`
	Currency        string         `json:"currency"`
}
type PostPayload struct {
	LeadID         string          `json:"lead_id"`
	DeliveredAt    string          `json:"delivered_at"`
	SubmittedAt    string          `json:"submitted_at,omitempty"`
	OfferID        string          `json:"offer_id,omitempty"`
	PriceCents     int             `json:"price_cents"`
	Currency       string          `json:"currency"`
	BuyerID        string          `json:"buyer_id"`
	BuyerReference string          `json:"buyer_reference,omitempty"`
	Pricing        map[string]any  `json:"pricing,omitempty"`
	Consumer       json.RawMessage `json:"consumer"`
	Location       json.RawMessage `json:"location"`
	Compliance     json.RawMessage `json:"compliance,omitempty"`
	Attributes     map[string]any  `json:"attributes"`
	Provenance     json.RawMessage `json:"provenance,omitempty"`
	Call           json.RawMessage `json:"call,omitempty"`
	Attachments    []map[string]any `json:"attachments,omitempty"`
}
type BidPayload struct {
	PingID                  string `json:"ping_id"`
	Decision                string `json:"decision"`
	BidPriceCents           int    `json:"bid_price_cents"`
	Currency                string `json:"currency"`
	EstimatedContactSeconds int    `json:"estimated_contact_seconds,omitempty"`
	BuyerReference          string `json:"buyer_reference,omitempty"`
	RejectReason            string `json:"reject_reason,omitempty"`
	CapacityRemaining       int    `json:"capacity_remaining,omitempty"`
}
type AckPayload struct {
	OriginalMessageID string           `json:"original_message_id"`
	Status            string           `json:"status"`
	Errors            []map[string]any `json:"errors,omitempty"`
	LeadID            string           `json:"lead_id,omitempty"`
	RequestID         string           `json:"request_id,omitempty"`
	RejectionReason   string           `json:"rejection_reason,omitempty"`
}
type EventPayload struct {
	LeadID            string         `json:"lead_id"`
	Event             string         `json:"event"`
	Timestamp         string         `json:"timestamp"`
	Details           map[string]any `json:"details,omitempty"`
	ExternalReference string         `json:"external_reference,omitempty"`
}
type OfferModel struct {
	OfferID         string         `json:"offer_id"`
	BuyerID         string         `json:"buyer_id"`
	TenantID        string         `json:"tenant_id,omitempty"`
	Active          bool           `json:"active,omitempty"`
	RoutingMode     string         `json:"routing_mode,omitempty"`
	Vertical        string         `json:"vertical"`
	SchemaVersion   string         `json:"schema_version,omitempty"`
	Countries       []string       `json:"countries"`
	StateRegions    []string       `json:"state_regions,omitempty"`
	PostalCodes     []string       `json:"postal_codes,omitempty"`
	Channels        []string       `json:"channels,omitempty"`
	FloorPriceCents int            `json:"floor_price_cents"`
	Currency        string         `json:"currency"`
	Extensions      map[string]any `json:"extensions,omitempty"`
	AllowedPublisherIDs []string `json:"allowed_publisher_ids,omitempty"`
	AllowedBrandIDs []string `json:"allowed_brand_ids,omitempty"`
	AttributeEquals map[string]any `json:"attribute_equals,omitempty"`
	AttributeIn map[string]any `json:"attribute_in,omitempty"`
	MonthlyMinimumPayable int `json:"monthly_minimum_payable,omitempty"`
	MonthlyMaximumPayable int `json:"monthly_maximum_payable,omitempty"`
	MonthlyQuotaTimezone string `json:"monthly_quota_timezone,omitempty"`
	MonthlyQuotaPolicy string `json:"monthly_quota_policy,omitempty"`
	PayableRules map[string]any `json:"payable_rules,omitempty"`
	CallRoutingMode string `json:"call_routing_mode,omitempty"`
	ConnectTimeoutSeconds int `json:"connect_timeout_seconds,omitempty"`
}
