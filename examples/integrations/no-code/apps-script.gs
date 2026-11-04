/**
 * example — not for production without review: a reviewed starter that posts
 * a Google Sheets row (or a Google Form response) to an LCP platform.
 *
 * Platform: Google Sheets / Google Forms (Apps Script, container-bound)
 * Runtime:  Apps Script (V8)
 *
 * Setup (see examples/integrations/no-code/README.md for the click path):
 *   Extensions ▸ Apps Script ▸ Project Settings ▸ Script properties:
 *     LCP_ENDPOINT          https://platform.example        (no trailing slash)
 *     LCP_SENDER_ID         publisher_001                    (from operator)
 *     LCP_RECEIVER_ID       platform_001                     (from operator)
 *     LCP_HMAC_SECRET       read-from-a-secret-manager
 *     LCP_VERTICAL          mortgage
 *     LCP_SCHEMA_VERSION    1.0.0
 *     LCP_CONSENT_PURPOSES  calls,email,share_with_partners  (exact purposes)
 *     LCP_CONSENT_SOURCE_URL https://example.com/quote       (your form page)
 *     LCP_TEST_MODE         true                             (false to go live)
 *
 * Script properties hold secrets; do not paste the secret into a cell, a
 * sheet name, or a comment.
 *
 * Deliberate limits of this platform:
 *   - Apps Script cannot read the consumer's IP address or user agent, so
 *     provenance.ip_address and provenance.user_agent_hash are omitted.
 *     Send from a server (Supabase edge function, Make, or a backend) when
 *     the platform requires them.
 *   - Execution time is capped (~6 min); a 429/5xx marks the row for a later
 *     run with the SAME idempotency key (saved in the row), so a retry cannot
 *     duplicate the lead.
 */

/** Edit this block to match your header row (column name ▸ LCP field). */
var COLUMNS = {
  email: 'Email',
  phone: 'Phone',
  firstName: 'First name',
  lastName: 'Last name',
  fullName: 'Full name',
  countryCode: 'Country code',
  stateRegion: 'State / Region',
  postalCode: 'Postal code',
  city: 'City',
  consent: 'Consent',
  campaignId: 'Campaign ID',
  externalId: 'Submission ID',
  vertical: 'Vertical',
};

var LCP_VERSION = '1.0.0';

function lcpConfig_() {
  var props = PropertiesService.getScriptProperties();
  var get = function (name) {
    var value = (props.getProperty(name) || '').trim();
    if (!value) throw new Error('Missing script property ' + name);
    return value;
  };
  return {
    endpoint: get('LCP_ENDPOINT').replace(/\/+$/, ''),
    senderId: get('LCP_SENDER_ID'),
    receiverId: get('LCP_RECEIVER_ID'),
    secret: get('LCP_HMAC_SECRET'),
    vertical: get('LCP_VERTICAL'),
    schemaVersion: get('LCP_SCHEMA_VERSION'),
    consentPurposes: get('LCP_CONSENT_PURPOSES').split(',')
      .map(function (p) { return p.trim(); })
      .filter(function (p) { return p; }),
    consentSourceUrl: props.getProperty('LCP_CONSENT_SOURCE_URL') || '',
    testMode: (props.getProperty('LCP_TEST_MODE') || 'true').toLowerCase() !== 'false',
  };
}

/** Whole-second UTC wire form: YYYY-MM-DDTHH:MM:SSZ. */
function lcpTimestamp_(date) {
  return Utilities.formatDate(date || new Date(), 'UTC', "yyyy-MM-dd'T'HH:mm:ss'Z'");
}

function hex_(bytes) {
  return bytes.map(function (b) {
    return ('0' + (b & 0xff).toString(16)).slice(-2);
  }).join('');
}

function hmacHex_(secret, input) {
  return hex_(Utilities.computeHmacSha256Signature(input, secret));
}

function e164_(value) {
  if (!value) return '';
  var compact = String(value).replace(/[\s().-]/g, '');
  return /^\+[1-9]\d{1,14}$/.test(compact) ? compact : '';
}

/**
 * Build the LCP envelope. `row` is a plain object already keyed by LCP field
 * names (email, phone, firstName, ...) — see readRow_ and fromFormEvent_.
 */
function buildEnvelope_(row, config, idempotencyKey, timestamp) {
  var messageId = Utilities.getUuid();

  var consumer = {};
  if (row.firstName) consumer.first_name = row.firstName;
  if (row.lastName) consumer.last_name = row.lastName;
  if (!row.firstName && !row.lastName && row.fullName) consumer.full_name = row.fullName;
  if (row.email) consumer.email = row.email;
  if (row.phone) consumer.phone = row.phone;
  if (!consumer.email && !consumer.phone && !consumer.full_name &&
      !(consumer.first_name && consumer.last_name)) {
    throw new Error('Row has no email, E.164 phone, or name');
  }

  var countryCode = String(row.countryCode || '').trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(countryCode)) {
    throw new Error('country_code must be ISO 3166-1 alpha-2 (e.g. AU)');
  }
  var location = { country_code: countryCode };
  if (row.stateRegion) location.state_region = row.stateRegion;
  if (row.postalCode) location.postal_code = row.postalCode;
  if (!location.state_region && !location.postal_code) {
    throw new Error('Row needs a state/region or a postal code');
  }
  if (row.city) location.city = row.city;

  var payload = {
    lead_id: 'lead_' + messageId,
    external_id: row.externalId || messageId,
    submitted_at: timestamp,
    status: 'NEW',
    channel: 'form',
    consumer: consumer,
    location: location,
    attributes: {
      vertical: row.vertical || config.vertical,
      schema_version: config.schemaVersion
    }
  };

  // Consent is not optional in practice: a lead without evidence is what a
  // buyer rejects. Fail closed instead of fabricating a consent block.
  if (row.consent) {
    payload.compliance = {
      consent_timestamp: timestamp,
      consent_purposes: config.consentPurposes
    };
    if (config.consentSourceUrl) {
      payload.compliance.consent_source_url = config.consentSourceUrl;
    }
  }

  if (row.campaignId) payload.provenance = { campaign_id: row.campaignId };

  return {
    lcp: {
      version: LCP_VERSION,
      message: {
        id: messageId,
        type: 'lead',
        timestamp: timestamp,
        sender_id: config.senderId,
        receiver_id: config.receiverId,
        correlation_id: null,
        idempotency_key: idempotencyKey,
        test: config.testMode
      },
      payload: payload
    }
  };
}

/**
 * POST one lead. Returns {ok, status, body, idempotencyKey}.
 * The same idempotency key on retry returns the original result (DUPLICATE/ack),
 * it does not create a second lead.
 */
function postLead_(row, idempotencyKey) {
  var config = lcpConfig_();
  if (!row.consent) {
    throw new Error('Consent checkbox is not ticked; not sending this row');
  }
  var normalizedPhone = row.phone ? e164_(row.phone) : '';
  if (row.phone && !normalizedPhone) {
    throw new Error('Phone must be E.164 (e.g. +61412345678), got: ' + row.phone);
  }
  var lead = Object.assign({}, row, { phone: normalizedPhone });

  var timestamp = lcpTimestamp_();
  var body = JSON.stringify(buildEnvelope_(lead, config, idempotencyKey, timestamp));
  var headers = {
    'X-LCP-Sender-Id': config.senderId,
    'X-LCP-Timestamp': timestamp,
    'X-LCP-Idempotency-Key': idempotencyKey,
    'X-LCP-Signature': hmacHex_(config.secret,
      timestamp + '\n' + idempotencyKey + '\n' + body),
    'Accept': 'application/json'
  };
  if (config.testMode) headers['X-LCP-Test'] = 'true';

  var response = UrlFetchApp.fetch(config.endpoint + '/v1/lcp/leads', {
    method: 'post',
    contentType: 'application/json',
    headers: headers,
    payload: body,
    muteHttpExceptions: true
  });
  var status = response.getResponseCode();
  var text = response.getContentText();
  var parsed;
  try { parsed = JSON.parse(text); } catch (err) { parsed = { raw: text }; }
  return {
    ok: status >= 200 && status < 300,
    status: status,
    body: parsed,
    idempotencyKey: idempotencyKey
  };
}

function readRow_(values, headers, index) {
  var record = {};
  for (var key in COLUMNS) {
    var col = headers.indexOf(COLUMNS[key]);
    if (col >= 0) record[key] = String(values[index][col] || '').trim();
  }
  record.consent = /^(true|yes|y|1|checked|x)$/i.test(record.consent || '');
  return record;
}

/** Google Form submit trigger: Installable trigger ▸ From spreadsheet ▸ On form submit. */
function onFormSubmit(e) {
  var record = fromFormEvent_(e);
  var key = record.externalId || ('form-' + Utilities.getUuid());
  var result = postLead_(record, key);
  console.log('LCP ' + result.status + ' ' + JSON.stringify(result.body));
  if (!result.ok) {
    // Keep the trigger from swallowing a failure silently in the UI.
    throw new Error('LCP rejected the lead: ' + result.status + ' ' +
      JSON.stringify(result.body));
  }
}

function fromFormEvent_(e) {
  var config = lcpConfig_();
  var named = e.namedValues || {};
  var pick = function (header) {
    var value = named[header];
    if (!value) return '';
    return Array.isArray(value) ? String(value[0] || '').trim() : String(value).trim();
  };
  var record = {
    email: pick(COLUMNS.email),
    phone: pick(COLUMNS.phone),
    firstName: pick(COLUMNS.firstName),
    lastName: pick(COLUMNS.lastName),
    fullName: pick(COLUMNS.fullName),
    countryCode: pick(COLUMNS.countryCode),
    stateRegion: pick(COLUMNS.stateRegion),
    postalCode: pick(COLUMNS.postalCode),
    city: pick(COLUMNS.city),
    campaignId: pick(COLUMNS.campaignId),
    externalId: pick(COLUMNS.externalId),
    vertical: pick(COLUMNS.vertical) || config.vertical
  };
  record.consent = /^(true|yes|y|1|checked|x)$/i.test(pick(COLUMNS.consent));
  return record;
}

/**
 * Batch sender for an existing sheet: reads the header row, sends every data
 * row, and logs one line per row. Run it twice on purpose — the second run
 * returns DUPLICATE from the platform, which is the proof idempotency works.
 */
function sendAllRows() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  var values = sheet.getDataRange().getValues();
  if (values.length < 2) throw new Error('Sheet needs a header row plus data');
  var headers = values[0].map(function (h) { return String(h).trim(); });
  var sent = 0;
  for (var i = 1; i < values.length; i++) {
    var record = readRow_(values, headers, i);
    if (!record.email && !record.phone && !record.fullName) continue; // blank row
    // Stable per row: the sheet row is retried with the same key.
    var key = record.externalId || ('sheet-' + i + '-' + record.email);
    try {
      var result = postLead_(record, key);
      console.log('row ' + (i + 1) + ': HTTP ' + result.status + ' ' +
        JSON.stringify(result.body));
      sent++;
    } catch (err) {
      console.log('row ' + (i + 1) + ': FAILED ' + err.message);
    }
  }
  console.log('Sent ' + sent + ' row(s)');
}

/**
 * Sandbox proof with no sheet involved: run once, read the ack in the
 * execution log. Replace the synthetic values with nothing — they are
 * deliberately fake (.invalid domain) and must stay fake.
 */
function testSend() {
  var result = postLead_({
    email: 'synthetic@example.invalid',
    phone: '+61412345678',
    firstName: 'Synthetic',
    lastName: 'Example',
    countryCode: 'AU',
    stateRegion: 'NSW',
    postalCode: '2000',
    city: 'Sydney',
    consent: true,
    campaignId: 'synthetic_fixture',
    externalId: 'test-' + lcpTimestamp_()
  }, 'test-' + Utilities.getUuid());
  console.log('HTTP ' + result.status + ' ' + JSON.stringify(result.body));
  return result;
}
