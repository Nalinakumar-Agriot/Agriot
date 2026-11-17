/**
 * AgriOT Universal Button Loader
 * Automatically adds a spinner loader to all Save, Submit, Update, and Action buttons across the application.
 */
(function() {
    'use strict';

    function getLoadingText(btn, customText) {
        if (customText) return customText;
        var text = (btn.textContent || '').trim().toLowerCase();
        if (text.includes('save changes') || text.includes('update')) return 'Updating...';
        if (text.includes('save') || text.includes('add') || text.includes('create')) return 'Saving...';
        if (text.includes('submit')) return 'Submitting...';
        if (text.includes('delete') || text.includes('remove')) return 'Deleting...';
        if (text.includes('record')) return 'Recording...';
        if (text.includes('pay') || text.includes('proceed')) return 'Processing...';
        if (text.includes('upload') || text.includes('import')) return 'Uploading...';
        if (text.includes('send') || text.includes('otp')) return 'Sending...';
        if (text.includes('login') || text.includes('sign in')) return 'Signing in...';
        return 'Processing...';
    }

    window.setButtonLoading = function(btn, customText) {
        if (!btn) return;
        if (typeof btn === 'string') btn = document.querySelector(btn);
        if (!btn || btn.getAttribute('data-agriot-loading') === 'true') return;

        // Never touch cancel / dismiss / toggle buttons
        if (btn.hasAttribute('data-bs-dismiss') || btn.classList.contains('btn-close') || btn.classList.contains('close') || btn.hasAttribute('data-no-loader')) return;
        var toggleAttr = btn.getAttribute('data-bs-toggle') || '';
        if (toggleAttr === 'dropdown' || toggleAttr === 'tab' || toggleAttr === 'pill' || toggleAttr === 'collapse') return;

        // Preserve original content and size
        btn.setAttribute('data-agriot-loading', 'true');
        btn.setAttribute('data-original-html', btn.innerHTML);
        var currentWidth = btn.getBoundingClientRect().width;
        if (currentWidth > 0) {
            btn.style.minWidth = Math.ceil(currentWidth) + 'px';
        }

        var loadingText = getLoadingText(btn, customText);
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true" style="vertical-align: -2px;"></span> ' + loadingText;
        btn.disabled = true;
        btn.classList.add('disabled', 'btn-loading-state');
    };

    window.resetButtonLoading = function(btn) {
        if (!btn) return;
        if (typeof btn === 'string') btn = document.querySelector(btn);
        if (!btn || btn.getAttribute('data-agriot-loading') !== 'true') return;

        var origHtml = btn.getAttribute('data-original-html');
        if (origHtml !== null) {
            btn.innerHTML = origHtml;
        }
        btn.disabled = false;
        btn.classList.remove('disabled', 'btn-loading-state');
        btn.removeAttribute('data-agriot-loading');
        btn.removeAttribute('data-original-html');
        btn.style.minWidth = '';
    };

    window.resetAllButtonLoaders = function() {
        document.querySelectorAll('[data-agriot-loading="true"]').forEach(function(b) {
            window.resetButtonLoading(b);
        });
    };

    function isActionButton(btn) {
        if (!btn) return false;
        if (btn.disabled || btn.getAttribute('data-agriot-loading') === 'true') return false;

        // Ignore dismiss, close, toggle, navigation, tabs
        if (btn.hasAttribute('data-bs-dismiss') || btn.classList.contains('btn-close') || btn.classList.contains('close') || btn.hasAttribute('data-no-loader')) return false;
        var toggleAttr = btn.getAttribute('data-bs-toggle') || '';
        if (toggleAttr && toggleAttr !== 'tooltip') return false;
        if (btn.classList.contains('nav-link') || btn.classList.contains('page-link') || btn.classList.contains('dropdown-item')) return false;
        if (btn.type === 'reset') return false;

        var text = (btn.textContent || '').trim().toLowerCase();
        var id = (btn.id || '').toLowerCase();
        var name = (btn.getAttribute('name') || '').toLowerCase();
        var inFooter = btn.closest('.modal-footer, .card-footer');

        if (btn.type === 'submit') return true;

        var isActionText = /^(save|submit|update|create|add |record|delete|yes|confirm|proceed|pay|send|login|sign in)/i.test(text) ||
                           text === 'save' || text === 'submit' || text === 'update' || text === 'record' || text === 'delete' || text === 'yes, delete';

        var isActionId = /save|submit|update|record|delete|btn_add|add_btn/i.test(id) || /save|submit|update/i.test(name);

        if (inFooter && (btn.classList.contains('btn-primary') || btn.classList.contains('btn-success') || btn.classList.contains('btn-warning') || btn.classList.contains('btn-danger'))) {
            return true;
        }

        return isActionText || isActionId;
    }

    // 1. Form submit listener (for all standard and AJAX forms)
    document.addEventListener('submit', function(e) {
        var form = e.target;
        if (!form || form.tagName !== 'FORM') return;

        if (form.checkValidity && !form.checkValidity()) return;

        var submitBtn = form.querySelector('button[type="submit"]:not([disabled]), input[type="submit"]:not([disabled]), button[id*="submit"]:not([disabled]), button[id*="save"]:not([disabled]), .modal-footer button.btn-primary:not([disabled]), .modal-footer button.btn-success:not([disabled])');
        if (submitBtn) {
            window.setButtonLoading(submitBtn);
        }
    }, true);

    // 2. Click listener for action buttons with onclick or AJAX triggers
    document.addEventListener('click', function(e) {
        var btn = e.target.closest('button, input[type="submit"], a.btn');
        if (!btn) return;

        // If inside form with type="submit", let submit listener handle it
        if (btn.type === 'submit' && btn.closest('form')) {
            return;
        }

        if (isActionButton(btn)) {
            var form = btn.closest('form');
            if (form && form.checkValidity && !form.checkValidity()) {
                if (form.reportValidity) form.reportValidity();
                return;
            }

            window.setButtonLoading(btn);

            // Safety recovery: if modal closes or after 3s if no AJAX in flight, reset
            setTimeout(function() {
                if (btn && btn.getAttribute('data-agriot-loading') === 'true') {
                    var modal = btn.closest('.modal');
                    var isModalHidden = modal && !modal.classList.contains('show');
                    var isAjaxActive = (window.jQuery && window.jQuery.active > 0);
                    if (isModalHidden || !isAjaxActive) {
                        window.resetButtonLoading(btn);
                    }
                }
            }, 3000);
        }
    }, false);

    // 3. Auto-recovery on jQuery AJAX completion or error
    if (window.jQuery) {
        $(document).ajaxComplete(function() {
            setTimeout(window.resetAllButtonLoaders, 300);
        });
        $(document).ajaxError(function() {
            window.resetAllButtonLoaders();
        });
    }

    // 4. Auto-recovery when modal is hidden
    document.addEventListener('hidden.bs.modal', function(e) {
        if (e.target) {
            e.target.querySelectorAll('[data-agriot-loading="true"]').forEach(function(b) {
                window.resetButtonLoading(b);
            });
        }
    });

    // 5. Auto-recovery on back navigation
    window.addEventListener('pageshow', function() {
        window.resetAllButtonLoaders();
    });
})();
