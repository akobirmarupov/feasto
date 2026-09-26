/*
 * Bron formasi: xona / zal / bo'sh vaqt ro'yxatlari faqat tanlangan
 * biznesga (va xona/zalga) tegishli yozuvlarni ko'rsatsin.
 *
 * Django avtomatik to'ldirish (select2) so'rovlariga `business_id` va
 * `room_id` parametrlari qo'shiladi — server tomonda
 * `get_search_results` shu bo'yicha filtrlaydi. Yakuniy tekshiruv baribir
 * modelning `clean()` metodida.
 */
(function () {
    "use strict";

    var DEPENDENT = {
        room: ["business"],
        hall: ["business"],
        availability: ["business", "room"],
    };

    function value(name) {
        var el = document.getElementById("id_" + name);
        return el ? el.value : "";
    }

    function clear(name) {
        var el = document.getElementById("id_" + name);
        if (!el || !el.value) return;
        if (window.django && django.jQuery) {
            django.jQuery(el).val(null).trigger("change");
        } else {
            el.value = "";
        }
    }

    function install($) {
        $.ajaxPrefilter(function (options) {
            if (!options.url || options.url.indexOf("/autocomplete/") === -1) return;
            if (options.url.indexOf("model_name=reservation") === -1 &&
                String(options.data || "").indexOf("model_name=reservation") === -1) return;

            var match = /field_name=(\w+)/.exec(options.url + "&" + (options.data || ""));
            var parents = match && DEPENDENT[match[1]];
            if (!parents) return;

            var extra = parents
                .filter(function (name) { return value(name); })
                .map(function (name) { return name + "_id=" + encodeURIComponent(value(name)); })
                .join("&");
            if (extra) {
                options.url += (options.url.indexOf("?") === -1 ? "?" : "&") + extra;
            }
        });

        // Biznes almashsa — eski xona/zal/sana endi mos emas.
        $(document).on("change", "#id_business", function () {
            clear("room");
            clear("hall");
            clear("availability");
        });
        $(document).on("change", "#id_room, #id_hall", function () {
            clear("availability");
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        if (window.django && django.jQuery) install(django.jQuery);
    });
})();
