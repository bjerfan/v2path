import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "mainWindow"
    width: 1160; height: 800
    minimumWidth: 960; minimumHeight: 660
    visible: false
    title: "V2Path " + bridge.view.version
    color: "#111316"
    font.family: "Segoe UI"
    font.pixelSize: 12
    readonly property color ink: "#EDF1F7"
    readonly property color muted: "#ADB7C7"
    readonly property color accent: "#718CFF"
    readonly property bool idle: bridge.view.state === "idle"
    readonly property bool connected: bridge.view.state === "running"
    readonly property bool busy: bridge.view.state === "starting" || bridge.view.state === "stopping"
    readonly property bool motion: bridge.view.animations
    palette.window: "#191C21"
    palette.windowText: ink
    palette.base: "#121519"
    palette.alternateBase: "#222832"
    palette.text: ink
    palette.button: "#272D36"
    palette.buttonText: ink
    palette.mid: "#343B47"
    palette.highlight: accent
    palette.highlightedText: "#101520"
    onClosing: function(close) { close.accepted = false; bridge.action("hide") }

    component Card: Pane {
        padding: 16
        background: Rectangle {
            color: "#191C21"; radius: 12
            border.color: "#343B47"
        }
    }
    component Heading: Label {
        color: root.ink
        font.pixelSize: 13; font.weight: Font.DemiBold
    }
    component Hint: Label {
        color: root.muted; font.pixelSize: 11
        wrapMode: Text.WordWrap
    }
    component ActionButton: Button {
        id: actionButton
        implicitHeight: 34; implicitWidth: 96
        padding: 10
        hoverEnabled: true
        opacity: enabled ? 1 : 0.42
        contentItem: Text {
            text: actionButton.text; color: root.ink
            font.family: "Segoe UI"; font.pixelSize: 11
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 7
            color: actionButton.down ? "#3A4353" : actionButton.hovered ? "#303A49" : "#272D36"
            border.color: actionButton.activeFocus ? root.accent : "#3B4351"
            Behavior on color { ColorAnimation { duration: root.motion ? 140 : 0 } }
        }
    }
    component Entry: TextField {
        implicitHeight: 36
        padding: 9
        color: root.ink; placeholderTextColor: "#939FB2"
        selectionColor: root.accent; selectedTextColor: "#101520"
        selectByMouse: true
        background: Rectangle {
            color: "#121519"; radius: 7
            border.color: parent.activeFocus ? root.accent : "#343B47"
        }
        opacity: enabled ? 1 : 0.5
    }
    component Choice: ComboBox {
        id: choice
        readonly property string selectedPopupTextColor: popup.contentItem.currentItem ? popup.contentItem.currentItem.contentItem.color.toString() : ""
        function openPopupForCheck() { popup.open() }
        function closePopupForCheck() { popup.close() }
        implicitHeight: 36
        leftPadding: 10; rightPadding: 28
        font.pixelSize: 12
        background: Rectangle {
            color: choice.enabled ? "#18202A" : "#263247"; radius: 7
            border.color: choice.activeFocus ? root.accent : choice.enabled ? "#58667D" : "#7184A5"
        }
        contentItem: Text {
            text: choice.displayText; color: root.ink; font: choice.font
            verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
        }
        delegate: ItemDelegate {
            id: option
            required property var modelData
            required property int index
            objectName: choice.objectName + "Option" + index
            width: choice.popup.width - 12
            height: 42
            padding: 10
            highlighted: choice.highlightedIndex === index
            contentItem: Text {
                objectName: option.objectName + "Text"
                text: String(option.modelData)
                color: "#F3F6FC"
                font: choice.font
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            background: Rectangle {
                radius: 6
                color: option.index === choice.currentIndex ? "#33476B" : option.highlighted || option.hovered ? "#28384F" : "#1D242E"
                border.color: option.index === choice.currentIndex ? "#8BA6FF" : "transparent"
            }
        }
        popup: Popup {
            y: choice.height + 4
            width: Math.min(root.width - 40, Math.max(choice.width, 500))
            padding: 6
            implicitHeight: Math.min(contentItem.implicitHeight, 350) + topPadding + bottomPadding
            background: Rectangle { color: "#1D242E"; radius: 9; border.color: "#64748B" }
            contentItem: ListView {
                clip: true
                implicitHeight: contentHeight
                model: choice.popup.visible ? choice.delegateModel : null
                currentIndex: choice.highlightedIndex
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { }
            }
        }
        // Selection remains readable while locked during a connection.
        opacity: 1
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 14

        Card {
            objectName: "connectionHeader"
            Layout.fillWidth: true
            Layout.preferredHeight: 88
            RowLayout {
                anchors.fill: parent
                spacing: 16
                Rectangle {
                    width: 44; height: 44; radius: 13; color: "transparent"
                    Image { anchors.fill: parent; source: "../assets/secondnet.png"; fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    Label { text: "V2Path"; color: root.ink; font.pixelSize: 24; font.weight: Font.DemiBold }
                    Hint { text: "Your connection. Your route." }
                }
                ColumnLayout {
                    spacing: 4
                    Label {
                        Layout.alignment: Qt.AlignRight
                        text: root.connected ? "Connected" : root.busy ? (bridge.view.state === "starting" ? "Connecting…" : "Stopping…") : "Ready to connect"
                        color: root.connected ? "#4DDEA0" : root.ink
                        font.pixelSize: 13; font.weight: Font.DemiBold
                    }
                    RowLayout {
                        Layout.alignment: Qt.AlignRight; spacing: 6
                        Rectangle { width: 6; height: 6; radius: 3; color: root.connected ? "#4DDEA0" : root.busy ? root.accent : "#788598" }
                        Hint { text: root.connected ? "VPN active" : root.busy ? "Please wait" : "Disconnected" }
                    }
                }
                Button {
                    id: connectButton
                    objectName: "connectButton"
                    Layout.preferredWidth: 158; Layout.preferredHeight: 46
                    hoverEnabled: true
                    enabled: !root.busy
                    Accessible.name: root.connected ? "Disconnect VPN" : "Connect VPN"
                    onClicked: bridge.action("toggle")
                    background: Rectangle {
                        radius: 11
                        color: root.connected ? (connectButton.hovered ? "#31956E" : "#247A56") : (connectButton.hovered ? "#829AFF" : "#718CFF")
                        border.color: connectButton.activeFocus ? "#EDF1F7" : "transparent"
                        border.width: 2
                        opacity: root.busy ? 0.45 : 1
                        Behavior on color { ColorAnimation { duration: root.motion ? 180 : 0 } }
                    }
                    contentItem: RowLayout {
                        spacing: 10
                        Item { Layout.fillWidth: true }
                        Item {
                            width: 20; height: 22
                            visible: !root.busy
                            readonly property color symbolColor: root.connected ? "#EDF1F7" : "#101520"
                            Rectangle { x: 1; y: 3; width: 18; height: 18; radius: 9; color: "transparent"; border.color: parent.symbolColor; border.width: 2 }
                            Rectangle { x: 5; y: 1; width: 10; height: 8; color: connectButton.background.color }
                            Rectangle { x: 9; y: 0; width: 2; height: 11; radius: 1; color: parent.symbolColor }
                        }
                        BusyIndicator { running: root.busy && root.motion; visible: root.busy; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                        Label {
                            text: root.busy ? "Please wait" : root.connected ? "Disconnect" : "Connect"
                            color: root.connected ? "#EDF1F7" : "#101520"
                            font.pixelSize: 13; font.weight: Font.DemiBold
                        }
                        Item { Layout.fillWidth: true }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            spacing: 14
            ColumnLayout {
                Layout.preferredWidth: 360
                Layout.minimumWidth: 300
                Layout.maximumWidth: 420
                Layout.fillWidth: true; Layout.fillHeight: true
                spacing: 14
                Card {
                    objectName: "internetCard"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 154
                    ColumnLayout {
                        anchors.fill: parent; spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Heading { text: "Internet interface"; Layout.fillWidth: true }
                            ActionButton { text: "Refresh"; implicitWidth: 66; enabled: root.idle && bridge.view.refreshEnabled; onClicked: bridge.action("refresh") }
                        }
                        Choice {
                            objectName: "adapterChoice"
                            Layout.fillWidth: true
                            enabled: root.idle
                            model: bridge.adapterLabels
                            currentIndex: bridge.view.adapterIndex
                            onActivated: bridge.setField("adapter", currentText)
                            Accessible.name: "Internet interface"
                            ToolTip.visible: hovered
                            ToolTip.text: currentText
                        }
                        Hint { text: bridge.view.adapterDetail; Layout.fillWidth: true; maximumLineCount: 3; elide: Text.ElideRight }
                    }
                }
                Card {
                    objectName: "profilesCard"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout {
                        anchors.fill: parent; spacing: 10
                        RowLayout {
                            Heading { text: "VPN profiles"; Layout.fillWidth: true }
                            Hint { text: bridge.view.profileCount + " saved" }
                        }
                        ActionButton {
                            objectName: "clipboardImportButton"
                            text: "Import from clipboard"
                            Layout.fillWidth: true; enabled: root.idle
                            onClicked: bridge.action("clipboard")
                            Accessible.description: "Import raw VLESS, VMess, or Trojan links from the clipboard. Shortcut: Control+V."
                        }
                        Hint { text: "Ctrl+V · VLESS / VMess / Trojan"; Layout.fillWidth: true }
                        Rectangle {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            color: "#121519"; radius: 8
                            ListView {
                                id: profiles
                                objectName: "profileList"
                                anchors.fill: parent; anchors.margins: 6
                                clip: true; spacing: 6
                                model: profileModel
                                boundsBehavior: Flickable.StopAtBounds
                                ScrollBar.vertical: ScrollBar { }
                                delegate: ItemDelegate {
                                    id: profile
                                    required property string profileId
                                    required property string profileName
                                    required property string profileKind
                                    required property string profileEndpoint
                                    required property bool isSelected
                                    required property bool isEnabled
                                    width: profiles.width - 10; height: 82
                                    enabled: isEnabled
                                    onClicked: bridge.selectProfile(profileId)
                                    Accessible.name: profileName + ", " + profileEndpoint + ", " + profileKind
                                    background: Rectangle {
                                        radius: 8
                                        color: profile.isSelected ? "#283454" : profile.hovered ? "#272E39" : "#191C21"
                                        border.color: profile.activeFocus || profile.isSelected ? root.accent : "#343B47"
                                        Behavior on color { ColorAnimation { duration: root.motion ? 140 : 0 } }
                                    }
                                    contentItem: ColumnLayout {
                                        spacing: 3
                                        Label { text: profile.profileName; color: root.ink; font.pixelSize: 12; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                                        Label { text: profile.profileEndpoint; color: root.ink; font.pixelSize: 12; elide: Text.ElideMiddle; Layout.fillWidth: true }
                                        Hint { text: profile.profileKind; elide: Text.ElideRight; Layout.fillWidth: true }
                                    }
                                    ToolTip.visible: hovered
                                    ToolTip.text: profileName + "\n" + profileEndpoint + "\n" + profileKind
                                }
                                Hint { anchors.centerIn: parent; visible: profiles.count === 0; text: "Copy a raw config link, then press Ctrl+V.\nSubscription links are ignored."; horizontalAlignment: Text.AlignHCenter; width: parent.width - 20; wrapMode: Text.WordWrap }
                            }
                        }
                        RowLayout {
                            Hint { text: bridge.view.configName; Layout.fillWidth: true; maximumLineCount: 2; elide: Text.ElideRight }
                            ActionButton { text: "Remove"; implicitWidth: 68; enabled: root.idle && bridge.view.profileCount > 0; onClicked: bridge.action("remove") }
                        }
                    }
                }
            }

            ScrollView {
                id: settingsScroll
                objectName: "settingsScroll"
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.minimumWidth: 440
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: settingsScroll.availableWidth
                    spacing: 10
                    Card {
                        objectName: "routingCard"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 116
                        ColumnLayout {
                            anchors.fill: parent; spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Heading { text: "Routing mode"; Layout.fillWidth: true }
                                Choice {
                                    Layout.preferredWidth: 190; enabled: root.idle
                                    model: ["Proxy only", "Per-App", "Xray TUN"]
                                    currentIndex: model.indexOf(bridge.view.modeLabel)
                                    onActivated: bridge.setField("mode", currentIndex === 1 ? "Discord per-app" : currentText)
                                    Accessible.name: "Routing mode"
                                }
                            }
                            Hint { text: bridge.view.routingDetail; Layout.fillWidth: true }
                        }
                    }
                    Card {
                        objectName: "applicationsCard"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 144
                        ColumnLayout {
                            anchors.fill: parent; spacing: 8
                            RowLayout {
                                Heading { text: "Applications"; Layout.fillWidth: true }
                                Hint { text: "Names or paths, separated by commas"; font.pixelSize: 10 }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Entry {
                                    objectName: "processEntry"
                                    Layout.fillWidth: true
                                    text: bridge.view.processes
                                    placeholderText: "Discord.exe"
                                    enabled: root.idle && bridge.view.mode !== "Proxy only"
                                    onTextEdited: bridge.setField("processes", text)
                                    Accessible.name: "Tunnel applications"
                                }
                                ActionButton { text: "Browse .exe"; enabled: root.idle && bridge.view.mode !== "Proxy only"; onClicked: bridge.action("browse") }
                            }
                            RowLayout {
                                Switch {
                                    id: autoRestartSwitch
                                    objectName: "discordAutoRestartSwitch"
                                    text: "Auto-restart Discord"
                                    Layout.fillWidth: true
                                    implicitHeight: 34; spacing: 8
                                    checked: bridge.view.discordAutoRestart
                                    enabled: (root.idle || root.connected) && bridge.view.mode === "Discord per-app"
                                    onToggled: bridge.setDiscordAutoRestart(checked)
                                    Accessible.name: "Automatically restart Discord after connecting"
                                    Accessible.description: "Turn off to restart Discord or reconnect voice manually. The preference is saved."
                                    opacity: enabled ? 1 : 0.5
                                    indicator: Rectangle {
                                        implicitWidth: 36; implicitHeight: 20
                                        y: (autoRestartSwitch.height - height) / 2
                                        radius: 10
                                        color: autoRestartSwitch.checked ? root.accent : "#343B47"
                                        border.color: autoRestartSwitch.activeFocus ? root.ink : "#67758B"
                                        border.width: autoRestartSwitch.activeFocus ? 2 : 1
                                        Rectangle {
                                            x: autoRestartSwitch.checked ? 19 : 3; y: 3
                                            width: 14; height: 14; radius: 7; color: root.ink
                                            Behavior on x { NumberAnimation { duration: root.motion ? 140 : 0 } }
                                        }
                                    }
                                    contentItem: Text {
                                        text: autoRestartSwitch.text; color: root.ink; font.pixelSize: 11
                                        leftPadding: autoRestartSwitch.indicator.width + autoRestartSwitch.spacing
                                        verticalAlignment: Text.AlignVCenter
                                    }
                                    ToolTip.visible: hovered
                                    ToolTip.text: "Off: restart Discord or reconnect voice yourself after connecting."
                                }
                                ActionButton { text: "Restart Discord"; implicitWidth: 108; enabled: root.connected && bridge.view.mode === "Discord per-app"; onClicked: bridge.action("restartDiscord") }
                                ActionButton { text: "Clear"; implicitWidth: 52; enabled: root.idle && bridge.view.mode !== "Proxy only"; onClicked: bridge.action("clearApps") }
                            }
                        }
                    }
                    Card {
                        objectName: "portsCard"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 96
                        RowLayout {
                            anchors.fill: parent; spacing: 14
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 4
                                Heading { text: "VPN SOCKS5"; font.pixelSize: 11 }
                                Entry { objectName: "vpnPort"; Layout.fillWidth: true; text: bridge.view.socksPort; enabled: root.idle; onTextEdited: bridge.setField("socksPort", text); Accessible.name: "VPN SOCKS5 port"; validator: IntValidator { bottom: 1; top: 65535 } }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 4
                                Heading { text: "Direct SOCKS5"; font.pixelSize: 11 }
                                Entry { objectName: "directPort"; Layout.fillWidth: true; text: bridge.view.directPort; enabled: root.idle; onTextEdited: bridge.setField("directPort", text); Accessible.name: "Direct SOCKS5 port"; validator: IntValidator { bottom: 1; top: 65535 } }
                            }
                        }
                    }
                    Card {
                        objectName: "logsCard"
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(160, settingsScroll.availableHeight - 386)
                        ColumnLayout {
                            anchors.fill: parent; spacing: 8
                            RowLayout {
                                Heading { text: "Connection log"; Layout.fillWidth: true }
                                ActionButton { text: "Follow"; implicitWidth: 60; visible: !logs.followTail; onClicked: { logs.followTail = true; logs.positionViewAtEnd() } }
                                ActionButton { text: "Clear log"; implicitWidth: 76; onClicked: bridge.action("clearLog") }
                            }
                            Rectangle {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                color: "#121519"; radius: 6
                                ListView {
                                    id: logs
                                    objectName: "logList"
                                    anchors.fill: parent; anchors.margins: 8
                                    property bool followTail: true
                                    clip: true; spacing: 4
                                    model: logModel
                                    boundsBehavior: Flickable.StopAtBounds
                                    onMovementStarted: followTail = false
                                    ScrollBar.vertical: ScrollBar { }
                                    delegate: Text {
                                        required property string lineText
                                        width: logs.width - 12
                                        text: lineText; color: root.muted
                                        font.family: "Consolas"; font.pixelSize: 11
                                        wrapMode: Text.WrapAnywhere
                                    }
                                    Hint { anchors.centerIn: parent; visible: logs.count === 0; text: "Connection activity will appear here." }
                                }
                            }
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true; spacing: 3
                Hint {
                    Layout.fillWidth: true
                    text: bridge.view.adapterName + "  ·  " + bridge.view.configName + "  ·  " + bridge.view.modeLabel
                    elide: Text.ElideRight; maximumLineCount: 1
                }
                Hint {
                    text: "VPN 127.0.0.1:" + bridge.view.socksPort + "    Direct 127.0.0.1:" + bridge.view.directPort
                    font.family: "Consolas"; font.pixelSize: 10
                }
            }
            RowLayout {
                spacing: 5
                Hint { text: "Made with From ZzeK"; font.pixelSize: 11 }
                Label { text: "❤️"; font.family: "Segoe UI Emoji"; font.pixelSize: 13; Accessible.name: "love" }
            }
            Hint { Layout.alignment: Qt.AlignRight | Qt.AlignBottom; text: "V" + bridge.view.version; font.pixelSize: 10; Layout.leftMargin: 18 }
        }
    }
    Connections {
        target: bridge
        function onLogUpdated() { if (logs.followTail) Qt.callLater(function() { logs.positionViewAtEnd() }) }
    }
}
