package com.acme.billing;

import javax.annotation.PostConstruct;
import java.math.BigDecimal;
import java.util.HashMap;
import java.util.Map;

/**
 * Gateway that talks to the card processor.
 */
public class PaymentGateway {

    private static final int TIMEOUT = 30;
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

    private String submit(String customerId, BigDecimal amount) {
        // Send the request to the processor and return the receipt id.
        String payload = customerId + ":" + amount.toPlainString();
        return ENDPOINT + "/receipt/" + payload.hashCode();
    }

    public BigDecimal pendingFor(String customerId) {
        return pending.getOrDefault(customerId, BigDecimal.ZERO);
    }

    public int timeoutSeconds() {
        return TIMEOUT;
    }
}
