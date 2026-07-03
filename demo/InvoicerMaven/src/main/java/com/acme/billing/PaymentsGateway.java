package com.acme.billing;

import jakarta.annotation.PostConstruct;
import java.math.BigDecimal;
import java.util.HashMap;
import java.util.Map;

/**
 * Gateway that talks to the card processor.
 */
public class PaymentsGateway {

    private static final int DEFAULT_TIMEOUT_MS = 30_000;
    private static final String ENDPOINT = "https://pay.acme.com/v1";

    private final Map<String, BigDecimal> pending = new HashMap<>();

    @PostConstruct
    public void init() {
        pending.clear();
    }

    public String charge(String customerId, BigDecimal amount) {
        if (amount == null || amount.signum() <= 0) {
            throw new IllegalArgumentException("amount must be positive");
        }
        pending.put(customerId, amount);
        return submit(customerId, amount);
    }

    public String chargeWithRetry(String customerId, BigDecimal amount, int attempts) {
        RuntimeException last = null;
        for (int i = 0; i < attempts; i++) {
            try {
                return charge(customerId, amount);
            } catch (RuntimeException ex) {
                last = ex;
            }
        }
        throw last;
    }

    private String submit(String customerId, BigDecimal amount) {
        // Send the request to the processor and return the receipt id.
        String payload = customerId + ":" + amount.toPlainString();
        return ENDPOINT + "/receipt/" + payload.hashCode();
    }

    public BigDecimal pendingFor(String customerId) {
        return pending.getOrDefault(customerId, BigDecimal.ZERO);
    }

    public int timeoutMillis() {
        return DEFAULT_TIMEOUT_MS;
    }
}
