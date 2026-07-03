package com.acme.store;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/** In-memory customer store (migrated unchanged from the classic build). */
public class CustomerRepository {

    private final List<String> customers = new ArrayList<>();

    public void add(String name) {
        customers.add(name);
    }

    public Optional<String> findByName(String name) {
        return customers.stream().filter(c -> c.equals(name)).findFirst();
    }

    public int count() {
        return customers.size();
    }
}
