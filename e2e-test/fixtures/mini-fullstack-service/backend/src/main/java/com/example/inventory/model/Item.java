package com.example.inventory.model;

@Entity
public class Item {

    @Id
    private Long id;

    private String name;

    private double price;

    public Long getId() {
        return id;
    }

    public String getName() {
        return name;
    }
}
