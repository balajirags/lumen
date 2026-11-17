package com.example.inventory.notify;

@Component
public class EmailNotifier implements Notifier {

    @Override
    public void notify(String message) {
        System.out.println("Emailing: " + message);
    }
}
