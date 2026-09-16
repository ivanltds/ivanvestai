self.addEventListener("push", (event) => {
  const payload = event.data ? event.data.json() : { title: "IvanVestAI", body: "Novo alerta" };
  event.waitUntil(
    self.registration.showNotification(payload.title || "IvanVestAI", {
      body: payload.body || "",
      icon: "/icon.png",
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow("/dashboard"));
});
