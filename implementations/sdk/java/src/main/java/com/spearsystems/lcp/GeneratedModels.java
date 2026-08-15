// GENERATED FROM schemas/ — do not edit manually.
package com.spearsystems.lcp;

import java.util.List;
import java.util.Map;

public final class GeneratedModels {
    private GeneratedModels() {}
    public record MessageModel(String id, String type, String timestamp, String senderId, String receiverId, String correlationId, String idempotencyKey, boolean test, Map<String, Object> security) {}
    public record EnvelopeModel(String version, MessageModel message, Object payload) {}
    public record LeadPayload(String leadId, String status, String channel, Object consumer, Object location, Map<String, Object> attributes, String externalId, String submittedAt, Object compliance, Object provenance, Object exclusivity, Object contactWindow, Object leadQuality, Object expiry, List<Object> attachments) {}
    public record CallPayload(String leadId, String status, String channel, Object consumer, Object location, Object call, String externalId, String submittedAt, Object compliance, Object provenance, Map<String, Object> attributes, Object expiry, Object exclusivity, List<Object> attachments) {}
    public record PingPayload(String pingId, String leadReference, String phoneHash, String countryCode, String vertical, int floorPriceCents, String currency, String publisherId, String offerId, String emailHash, Map<String, Object> attributes) {}
    public record PostPayload(String leadId, String deliveredAt, int priceCents, String currency, String buyerId, Object consumer, Object location, Map<String, Object> attributes, String submittedAt, String offerId, String buyerReference, Object pricing, Object compliance, Object provenance, Object call, List<Object> attachments) {}
    public record BidPayload(String pingId, String decision, int bidPriceCents, String currency, Integer estimatedContactSeconds, String buyerReference, String rejectReason, Integer capacityRemaining) {}
    public record AckPayload(String originalMessageId, String status, List<Object> errors, String leadId, String requestId, String rejectionReason) {}
    public record EventPayload(String leadId, String event, String timestamp, Map<String, Object> details, String externalReference) {}
    public record OfferModel(String offerId, String buyerId, String vertical, List<String> countries, int floorPriceCents, String currency, String tenantId, boolean active, String routingMode, String schemaVersion, Map<String, Object> extensions, List<String> allowedPublisherIds, List<String> allowedBrandIds, Map<String, Object> attributeEquals, Map<String, Object> attributeIn, Integer monthlyMinimumPayable, Integer monthlyMaximumPayable, String monthlyQuotaTimezone, String monthlyQuotaPolicy, Map<String, Object> payableRules, String callRoutingMode, Integer connectTimeoutSeconds) {}
}
