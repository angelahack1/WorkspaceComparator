package com.acme.util;

public final class StringHelper {

    private StringHelper() {
    }

    public static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    public static String initials(String fullName) {
        StringBuilder sb = new StringBuilder();
        for (String part : fullName.split("\\s+")) {
            if (!part.isEmpty()) {
                sb.append(Character.toUpperCase(part.charAt(0)));
            }
        }
        return sb.toString();
    }
}
