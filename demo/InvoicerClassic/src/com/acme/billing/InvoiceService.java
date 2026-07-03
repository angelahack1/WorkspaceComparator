package com.acme.billing;

import java.math.BigDecimal;
import java.time.LocalDate;

public class InvoiceService {

    public BigDecimal totalWithTax(BigDecimal net, BigDecimal taxRate) {
        return net.add(net.multiply(taxRate));
    }

    public String invoiceNumber(String customerId, LocalDate date) {
        return "INV-" + date.getYear() + "-" + customerId;
    }
}
