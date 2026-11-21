package com.agriot.farmer

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import org.junit.Rule
import org.junit.Test

class LoginSmokeTest {
    @get:Rule val compose=createAndroidComposeRule<MainActivity>()
    @Test fun appLaunchesAndRejectsInvalidPhoneWithoutSendingSms() {
        compose.onNodeWithText("Welcome back").assertIsDisplayed()
        compose.onNodeWithText("Mobile number").performTextInput("0000000000")
        compose.onNodeWithText("Send verification code").performScrollTo().performClick()
        compose.onAllNodesWithText("Enter a valid 10-digit mobile number").onFirst().assertExists()
    }
    @Test fun registrationOpensNativeFarmerForm() {
        compose.onNodeWithText("New farmer? Create an account").performScrollTo().performClick()
        compose.onNodeWithText("Create your farmer account").assertExists()
        compose.onNodeWithText("Full name *").assertExists()
    }
}
